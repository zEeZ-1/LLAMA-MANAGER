import asyncio
import uuid
from ..core.errors import ManagerError
from ..nodes.telemetry import now


class JobManager:
    def __init__(self, store, cfg):
        self.store, self.cfg = store, cfg
        self.records = {}
        self.tasks = {}
        self.submit_lock = asyncio.Lock()

    async def initialize(self):
        for record in (await self.store.all("jobs"))[-50:]:
            if record["state"] in {"queued", "running"}:
                record.update(
                    state="interrupted",
                    error={
                        "code": "manager_terminated",
                        "message": "管理后端已退出，请核对远端状态后重试",
                    },
                    updated_at=now(),
                )
                await self.store.put("jobs", record)
            self.records[record["id"]] = record

    async def phase(self, identifier, phase, message):
        record = self.records[identifier]
        record.update(phase=phase, message=message, updated_at=now())
        await self.store.put("jobs", record)

    async def submit(self, node_id, operation, work, secrets=(), idempotency_key=None):
        async with self.submit_lock:
            return await self._submit(
                node_id, operation, work, secrets, idempotency_key
            )

    async def _submit(self, node_id, operation, work, secrets, idempotency_key):
        if idempotency_key:
            for r in self.records.values():
                if r.get("idempotency_key") == idempotency_key:
                    if r["node_id"] != node_id or r["operation"] != operation:
                        raise ManagerError(
                            "idempotency_conflict", "重复操作标识已被其他任务使用"
                        )
                    return r
        for r in self.records.values():
            if (
                r["id"] in self.tasks
                and r["node_id"] == node_id
                and r["operation"] == operation
            ):
                return r
        if len(self.tasks) >= self.cfg.ssh.max_pending_commands:
            raise ManagerError("job_queue_full", "后台任务队列已满", 429)
        record = dict(
            id=str(uuid.uuid4()),
            node_id=node_id,
            operation=operation,
            state="queued",
            phase="queued",
            message="等待执行",
            error=None,
            result=None,
            created_at=now(),
            updated_at=now(),
            idempotency_key=idempotency_key,
        )
        self.records[record["id"]] = record
        await self.store.put("jobs", record)

        async def run():
            try:
                record["state"] = "running"
                await self.phase(record["id"], "running", "正在执行")
                result = await work(record["id"])
                record.update(
                    state="completed",
                    result=result,
                    phase="completed",
                    message="已完成",
                )
            except asyncio.CancelledError:
                record.update(
                    state="interrupted",
                    error={
                        "code": "task_interrupted",
                        "message": "任务已中断，后续操作前将核对远端实际状态",
                    },
                )
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                error = (
                    detail
                    if isinstance(detail, dict)
                    else {"code": "operation_failed", "message": str(exc)}
                )
                message = str(error.get("message", "操作失败"))
                for secret in secrets:
                    if secret:
                        message = message.replace(secret, "[redacted]")
                record.update(
                    state="failed",
                    error={
                        "code": error.get("code", "operation_failed"),
                        "message": message[:1800],
                    },
                )
            finally:
                record["updated_at"] = now()
                await self.store.put("jobs", record)
                self.tasks.pop(record["id"], None)
                completed = [
                    r for r in self.records.values() if r["id"] not in self.tasks
                ]
                for old in completed[:-50]:
                    self.records.pop(old["id"], None)
                    await self.store.delete("jobs", old["id"])

        self.tasks[record["id"]] = asyncio.create_task(run())
        return record

    def get(self, identifier):
        if identifier not in self.records:
            raise ManagerError("job_missing", "任务不存在", 404)
        return self.records[identifier]

    async def cancel(self, identifier):
        record = self.get(identifier)
        if task := self.tasks.get(identifier):
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if record["state"] in {"queued", "running"}:
                record.update(
                    state="interrupted",
                    error={"code": "task_interrupted", "message": "任务已取消"},
                    updated_at=now(),
                )
                await self.store.put("jobs", record)
            self.tasks.pop(identifier, None)
        return record

    async def close(self):
        await asyncio.gather(
            *(self.cancel(identifier) for identifier in list(self.tasks)),
            return_exceptions=True,
        )
