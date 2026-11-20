import asyncio
import logging
import uuid
from datetime import datetime, timezone
from fastapi import HTTPException
from ..process.port_allocator import PortAllocator

log = logging.getLogger(__name__)
ACTIVE = {"created", "starting", "ready", "stopping"}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class InstanceManager:
    def __init__(self, settings, catalog, repository, adapters, nodes):
        self.settings, self.catalog, self.repository = settings, catalog, repository
        self.adapters, self.nodes = adapters, nodes
        self.instances = {}
        self.locks = {}
        self.create_lock = asyncio.Lock()
        self.tasks = {}
        self.ports = PortAllocator(settings.instances)

    def get(self, instance_id):
        if instance_id not in self.instances:
            raise HTTPException(404, "Instance not found")
        return self.instances[instance_id]

    async def initialize(self):
        for record in await self.repository.all():
            self.instances[record["id"]] = record
            self.locks[record["id"]] = asyncio.Lock()
            if record["status"] in ACTIVE:
                adapter = self.adapters.get(record["runtime_type"])
                if not adapter or not adapter.get_status(record):
                    await self._transition(
                        record,
                        "failed",
                        "Previous inference process is absent or its identity changed",
                    )
                elif record["status"] == "stopping":
                    await self.destroy(record["id"])
                else:
                    # READY is re-established only after a fresh health probe.
                    await self._transition(record, "starting")
                    self._watch(record)

    async def _transition(self, record, status, error=None):
        allowed = {
            "created": {"starting", "stopping", "failed"},
            "starting": {"ready", "stopping", "failed"},
            "ready": {"starting", "stopping", "failed"},
            "stopping": {"stopped", "failed"},
            "failed": {"stopping"},
            "stopped": set(),
        }
        old = record["status"]
        if status != old and status not in allowed[old]:
            raise RuntimeError(f"Invalid instance transition: {old} -> {status}")
        record["status"] = status
        record["error_message"] = error
        if status == "ready":
            record["started_at"] = record["started_at"] or utcnow()
        if status in {"failed", "stopped"}:
            record["stopped_at"] = utcnow()
        await self.repository.save(record)

    async def create(self, request):
        async with self.create_lock:
            if request.node_id not in self.nodes:
                raise HTTPException(404, "Node not found")
            if (
                sum(r["status"] in ACTIVE for r in self.instances.values())
                >= self.settings.instances.max_active
            ):
                raise HTTPException(409, "Active instance limit reached")
            try:
                model = self.catalog.get_model(request.model_id)
                if model["node_id"] != request.node_id:
                    raise ValueError("Model does not belong to the selected node")
                used = {
                    r["runtime"]["internal_port"]
                    for r in self.instances.values()
                    if r["status"] in ACTIVE
                    or self.adapters[r["runtime_type"]].get_status(r)
                }
                port = self.ports.allocate(used)
            except (ValueError, OSError) as exc:
                raise HTTPException(409, str(exc)) from exc
            record = dict(
                id=str(uuid.uuid4()),
                name=request.name.strip(),
                node_id=request.node_id,
                model_id=request.model_id,
                model_name=model["name"],
                runtime_type=request.backend,
                config=request.config.model_dump(),
                status="created",
                runtime={
                    "pid": None,
                    "internal_port": port,
                    "endpoint": f"http://{self.settings.instances.host}:{port}",
                },
                error_message=None,
                created_at=utcnow(),
                started_at=None,
                stopped_at=None,
            )
            self.instances[record["id"]] = record
            self.locks[record["id"]] = asyncio.Lock()
            await self._transition(record, "starting")
            # Spawn completes before replying, while loading and health checks continue in background.
            async with self.locks[record["id"]]:
                try:
                    identity = await self.adapters[request.backend].start(record, model)
                    record["runtime"].update(identity)
                    await self.repository.save(record)
                    self._watch(record)
                except Exception as exc:
                    if record["runtime"].get("pid"):
                        await self.adapters[request.backend].stop(record)
                    await self._transition(record, "failed", str(exc))
            return record

    def _watch(self, record):
        task = asyncio.create_task(self._monitor(record))
        self.tasks[record["id"]] = task
        task.add_done_callback(lambda t: self.tasks.pop(record["id"], None))

    async def _monitor(self, record):
        adapter = self.adapters[record["runtime_type"]]
        deadline = (
            asyncio.get_running_loop().time()
            + self.settings.instances.startup_timeout_seconds
        )
        try:
            while record["status"] in {"starting", "ready"}:
                async with self.locks[record["id"]]:
                    if record["status"] not in {"starting", "ready"}:
                        return
                    if not adapter.get_status(record):
                        await adapter.cleanup_exited(record)
                        await self._transition(
                            record,
                            "failed",
                            "llama-server exited unexpectedly; inspect instance log",
                        )
                        return
                    if record["status"] == "starting":
                        if await adapter.health_check(record):
                            await self._transition(record, "ready")
                        elif asyncio.get_running_loop().time() > deadline:
                            await adapter.stop(record)
                            await self._transition(
                                record,
                                "failed",
                                "Model startup timed out; inspect instance log",
                            )
                            return
                    await adapter.maintain(record)
                await asyncio.sleep(0.5 if record["status"] == "starting" else 2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Instance monitor failed")
            await self._transition(record, "failed", str(exc))

    async def destroy(self, instance_id):
        record = self.get(instance_id)
        async with self.locks[instance_id]:
            if record["status"] == "stopped":
                return record
            await self._transition(record, "stopping")
            try:
                await self.adapters[record["runtime_type"]].stop(record)
                await self._transition(record, "stopped")
            except Exception as exc:
                await self._transition(record, "failed", str(exc))
                raise HTTPException(500, str(exc)) from exc
        return record

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def public(record):
        result = {k: v for k, v in record.items() if k != "runtime"}
        result["runtime"] = {
            k: v
            for k, v in record["runtime"].items()
            if k not in {"argv", "process_created_at", "endpoint"}
        }
        return result
