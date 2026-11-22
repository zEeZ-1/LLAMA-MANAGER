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
    def __init__(self, settings, catalog, repository, adapters, nodes, resources=None):
        self.resources = resources
        self.closing = False
        self.settings, self.catalog, self.repository = settings, catalog, repository
        self.adapters, self.nodes = adapters, nodes
        self.instances = {}
        self.locks = {}
        self.create_lock = asyncio.Lock()
        self.tasks = {}
        self.start_tasks = {}
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
                if adapter and adapter.get_status(record):
                    await adapter.stop(record)
                await self._transition(
                    record,
                    "failed",
                    "manager_terminated: previous manager exited; model is not reloaded",
                )
            elif record["status"] == "failed" and not record.get("error_detail"):
                if adapter := self.adapters.get(record["runtime_type"]):
                    detail = await adapter.failure_details(
                        record, record.get("error_message") or "实例运行失败"
                    )
                    record["error_message"] = detail["message"]
                    record["error_detail"] = detail
                    await self.repository.save(record)
        if self.resources:
            self.resources.instance_manager = self
            for instance_id in {
                c["instance_id"] for c in await self.resources.store.claims()
            }:
                await self.resources.release(instance_id)

    async def _transition(self, record, status, error=None, error_detail=None):
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
        record["error_detail"] = error_detail
        if status == "ready":
            record["started_at"] = record["started_at"] or utcnow()
        if status in {"failed", "stopped"}:
            record["stopped_at"] = utcnow()
        await self.repository.save(record)

    async def create(self, request):
        async with self.create_lock:
            if self.closing:
                raise HTTPException(503, "Manager is shutting down")
            if request.node_id not in self.nodes:
                raise HTTPException(404, "Node not found")
            if (
                sum(r["status"] in ACTIVE for r in self.instances.values())
                >= self.settings.instances.max_active
            ):
                raise HTTPException(409, "Active instance limit reached")
            try:
                model = self.catalog.get_model(request.model_id, request.config)
                request.config.variant_id = model["variant_id"]
                request.config.model_revision = model["revision"]
                if (
                    request.config.modality == "image_text"
                    and request.config.vision_device_id != "cpu"
                ):
                    local = await self.nodes["local"].get()
                    device = next(
                        (
                            d
                            for d in local["devices"]
                            if d["id"] == request.config.vision_device_id
                            and d["backend"] == "cuda"
                        ),
                        None,
                    )
                    if not device:
                        raise ValueError(
                            "vision_device_invalid: local GPU is unavailable"
                        )
                    if request.config.placement.mode == "rpc" and not any(
                        d.device_id == device["id"]
                        for d in request.config.placement.devices
                    ):
                        raise ValueError(
                            "vision_device_invalid: select the local vision GPU in placement"
                        )
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
                error_detail=None,
                created_at=utcnow(),
                started_at=None,
                stopped_at=None,
                capabilities={
                    "image_input": False,
                    "input_modalities": ["text"],
                    "output_modalities": ["text"],
                },
            )
            if request.config.placement.mode == "rpc":
                if not self.resources:
                    raise HTTPException(409, "RPC resource manager is unavailable")
                await self.resources.reserve(record["id"], request.config)
            self.instances[record["id"]] = record
            self.locks[record["id"]] = asyncio.Lock()
            try:
                await self._transition(record, "starting")
            except BaseException:
                if self.resources:
                    await self.resources.store.claim_state(record["id"], "released")
                self.instances.pop(record["id"], None)
                self.locks.pop(record["id"], None)
                raise
            task = asyncio.create_task(self._start(record, request.config))
            self.start_tasks[record["id"]] = task
            task.add_done_callback(
                lambda done: self.start_tasks.pop(record["id"], None)
            )
            return record

    async def _start(self, record, config):
        adapter = self.adapters[record["runtime_type"]]
        async with self.locks[record["id"]]:
            try:
                if config.placement.mode == "rpc":
                    record["runtime"]["placement"] = await self.resources.prepare(
                        record["id"], config
                    )
                # Revalidate the model immediately before launch, after remote preparations.
                model = self.catalog.get_model(record["model_id"], config)
                record["runtime"]["assets"] = {
                    k: model.get(k) for k in ("variant_id", "revision", "projector_id")
                }
                identity = await adapter.start(record, model)
                record["runtime"].update(identity)
                await self.repository.save(record)
                self._watch(record)
            except BaseException as exc:
                if record["runtime"].get("pid") and adapter.get_status(record):
                    await adapter.stop(record)
                exit_code = await adapter.cleanup_exited(record)
                detail = await self._failure_details(
                    record, adapter, exc, "startup_failed", exit_code
                )
                await self._transition(
                    record,
                    "failed",
                    "startup_cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else detail["message"],
                    None if isinstance(exc, asyncio.CancelledError) else detail,
                )
                if self.resources:
                    await self.resources.release(record["id"])
                if isinstance(exc, asyncio.CancelledError):
                    raise

    def _watch(self, record):
        task = asyncio.create_task(self._monitor(record))
        self.tasks[record["id"]] = task
        task.add_done_callback(lambda t: self.tasks.pop(record["id"], None))

    async def _monitor(self, record):
        adapter = self.adapters[record["runtime_type"]]
        deadline = asyncio.get_running_loop().time() + (
            self.settings.rpc.distributed_instance_startup_timeout_seconds
            if record["runtime"].get("placement")
            else self.settings.instances.startup_timeout_seconds
        )
        try:
            while record["status"] in {"starting", "ready"}:
                async with self.locks[record["id"]]:
                    if record["status"] not in {"starting", "ready"}:
                        return
                    if not adapter.get_status(record):
                        exit_code = await adapter.cleanup_exited(record)
                        detail = await adapter.failure_details(
                            record,
                            "llama-server exited unexpectedly; inspect instance log",
                            exit_code=exit_code,
                        )
                        await self._transition(
                            record,
                            "failed",
                            detail["message"],
                            detail,
                        )
                        if self.resources:
                            await self.resources.release(record["id"])
                        return
                    if self.resources and not await self.resources.healthy(record):
                        await adapter.stop(record)
                        detail = await adapter.failure_details(
                            record,
                            "RPC worker exited or its process identity changed",
                            "rpc_worker_lost",
                        )
                        await self._transition(
                            record,
                            "failed",
                            detail["message"],
                            detail,
                        )
                        await self.resources.release(record["id"])
                        return
                    if record["status"] == "starting":
                        if await adapter.health_check(record):
                            await self._transition(record, "ready")
                            if self.resources:
                                await self.resources.active(record["id"])
                        elif asyncio.get_running_loop().time() > deadline:
                            await adapter.stop(record)
                            detail = await adapter.failure_details(
                                record,
                                "Model startup timed out; inspect instance log",
                                "startup_timeout",
                            )
                            await self._transition(
                                record,
                                "failed",
                                detail["message"],
                                detail,
                            )
                            if self.resources:
                                await self.resources.release(record["id"])
                            return
                    await adapter.maintain(record)
                await asyncio.sleep(0.5 if record["status"] == "starting" else 2)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Instance monitor failed")
            if adapter.get_status(record):
                await adapter.stop(record)
            detail = await self._failure_details(
                record, adapter, exc, "runtime_monitor_failed"
            )
            await self._transition(record, "failed", detail["message"], detail)
            if self.resources:
                await self.resources.release(record["id"])

    async def _failure_details(self, record, adapter, exc, code, exit_code=None):
        detail = getattr(exc, "detail", None)
        if isinstance(detail, dict):
            message = str(detail.get("message", exc))
            code = str(detail.get("code", code))
        else:
            message = str(exc)
            prefix, separator, _ = message.partition(":")
            if separator and prefix.replace("_", "").isalpha() and "_" in prefix:
                code = prefix
        return await adapter.failure_details(record, message, code, exit_code)

    async def destroy(self, instance_id):
        record = self.get(instance_id)
        if starter := self.start_tasks.get(instance_id):
            starter.cancel()
            await asyncio.gather(starter, return_exceptions=True)
        async with self.locks[instance_id]:
            if record["status"] == "stopped":
                return record
            await self._transition(record, "stopping")
            try:
                await self.adapters[record["runtime_type"]].stop(record)
                await self._transition(record, "stopped")
                if self.resources:
                    await self.resources.release(instance_id)
            except Exception as exc:
                await self._transition(record, "failed", str(exc))
                raise HTTPException(500, str(exc)) from exc
        return record

    async def close(self):
        self.closing = True
        starters = list(self.start_tasks.values())
        for task in starters:
            task.cancel()
        await asyncio.gather(*starters, return_exceptions=True)
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for record in list(self.instances.values()):
            adapter = self.adapters[record["runtime_type"]]
            if record["status"] in ACTIVE or adapter.get_status(record):
                try:
                    await self.destroy(record["id"])
                except Exception:
                    log.exception("Failed to stop an owned instance during shutdown")

    @staticmethod
    def public(record):
        result = {k: v for k, v in record.items() if k != "runtime"}
        result["runtime"] = {
            k: v
            for k, v in record["runtime"].items()
            if k not in {"argv", "process_created_at", "endpoint"}
        }
        return result
