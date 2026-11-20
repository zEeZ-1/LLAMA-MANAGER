import asyncio

from ..core.errors import ManagerError


class ResourceManager:
    def __init__(self, remote, local_provider):
        self.remote = remote
        self.store = remote.store
        self.local_provider = local_provider
        self.instance_manager = None
        self.local_names = {}

    async def reserve(self, instance_id, config):
        if config.placement.mode == "local":
            return
        try:
            async with asyncio.timeout(0.5):
                await self.remote.operations.acquire()
        except TimeoutError:
            raise ManagerError(
                "node_operation_busy", "节点管理任务正在执行，请稍后创建实例"
            ) from None
        try:
            worker_ids = await self._validate(config, instance_id)
            await self.store.reserve(worker_ids, instance_id)
        finally:
            self.remote.operations.release()

    async def _validate(self, config, instance_id=None):
        placement = config.placement
        remote = self.remote
        worker_ids = list(
            dict.fromkeys(d.worker_id for d in placement.devices if d.worker_id)
        )
        if not worker_ids:
            raise ManagerError(
                "placement_invalid", "多机模式至少选择一个远端 RPC worker"
            )
        local = await self.local_provider.get()
        devices = {d["id"]: d for d in local["devices"]}
        for wid in worker_ids:
            worker = remote.worker(wid)
            node = remote.node(worker["node_id"])
            if node["connection_state"] != "online":
                raise ManagerError("node_offline", "所选远端节点的管理连接不可用")
            if worker.get("runtime_commit") != remote.cfg.rpc.expected_commit:
                raise ManagerError(
                    "version_mismatch", "所选 RPC worker 尚未部署匹配版本"
                )
            if worker["management_mode"] != "managed":
                raise ManagerError("worker_not_managed", "所选 worker 尚未验证接管")
            if worker.get("state") == "busy":
                raise ManagerError("rpc_in_use", "所选 RPC 正在被其他客户端使用")
            devices.update(
                {d["id"]: d for d in node.get("snapshot", {}).get("devices", [])}
            )
        for item in placement.devices:
            if item.device_id not in devices:
                raise ManagerError("device_unavailable", "所选设备已不存在")
            device = devices[item.device_id]
            if item.worker_id:
                if item.device_id not in remote.worker(item.worker_id)["device_ids"]:
                    raise ManagerError("device_mismatch", "设备不属于所选 RPC worker")
            elif device["node_id"] != "local":
                raise ManagerError("placement_invalid", "远端设备必须指定所属 worker")
        selected_devices = {
            d for wid in worker_ids for d in remote.worker(wid)["device_ids"]
        }
        for claim in await self.store.claims():
            if claim["instance_id"] == instance_id:
                continue
            occupied = remote.workers.get(claim["worker_id"], {})
            if selected_devices.intersection(occupied.get("device_ids", [])):
                raise ManagerError(
                    "worker_busy", f"所选 GPU 已被实例 {claim['instance_id']} 占用"
                )
        for other in remote.workers.values():
            if (
                other["id"] not in worker_ids
                and not other.get("deleted_at")
                and other.get("client_count")
                and selected_devices.intersection(other.get("device_ids", []))
            ):
                raise ManagerError("rpc_in_use", "同一 GPU 上的其他 RPC 正被客户端占用")
        return worker_ids

    async def prepare(self, instance_id, config):
        if config.placement.mode == "local":
            return None
        placement = config.placement
        remote = self.remote
        async with remote.operations:
            worker_ids = await self._validate(config, instance_id)
            claims = {c["worker_id"]: c for c in await self.store.claims()}
            if any(
                wid not in claims
                or claims[wid]["instance_id"] != instance_id
                or claims[wid]["state"] != "reserved"
                for wid in worker_ids
            ):
                raise ManagerError("claim_changed", "预留资源发生变化，实例不会启动")
            workers = [remote.worker(wid) for wid in worker_ids]
            local = await self.local_provider.get()
            devices = {d["id"]: d for d in local["devices"]}
            for worker in workers:
                devices.update(
                    {
                        d["id"]: d
                        for d in remote.node(worker["node_id"])
                        .get("snapshot", {})
                        .get("devices", [])
                    }
                )
            try:
                offset = 0
                mapping = {}
                endpoints = []
                versions = {}
                for worker in workers:
                    await remote.start_unlocked(worker)
                    endpoints.append(
                        f"{worker['endpoint']['host']}:{worker['endpoint']['port']}"
                    )
                    for index, device_id in enumerate(worker["device_ids"]):
                        mapping[(worker["id"], device_id)] = f"RPC{offset + index}"
                    offset += len(worker["device_ids"])
                    versions[worker["id"]] = {
                        "commit": worker["runtime_commit"],
                        "protocol": worker["protocol_version"],
                        "process_identity": worker["process_identity"],
                    }
                selected = []
                for item in placement.devices:
                    name = (
                        mapping[(item.worker_id, item.device_id)]
                        if item.worker_id
                        else "CUDA" + str(devices[item.device_id]["backend_index"])
                    )
                    selected.append(name)
                return {
                    "worker_ids": worker_ids,
                    "rpc_endpoints": endpoints,
                    "device_names": selected,
                    "tensor_split": [d.weight for d in placement.devices],
                    "worker_versions": versions,
                }
            except BaseException:
                await self._release_locked(instance_id)
                raise

    async def _release_locked(self, instance_id):
        claims = [
            c for c in await self.store.claims() if c["instance_id"] == instance_id
        ]
        if not claims:
            return
        record = (
            self.instance_manager.instances.get(instance_id)
            if self.instance_manager
            else None
        )
        if (
            record
            and record["runtime"].get("pid") is None
            and all(c["state"] == "reserved" for c in claims)
        ):
            await self.store.claim_state(instance_id, "released")
            return
        await self.store.claim_state(instance_id, "releasing")
        try:
            for claim in claims:
                worker = self.remote.worker(claim["worker_id"])
                await self.remote.refresh_worker(worker)
                if worker["state"] == "stopped":
                    continue
                await self.remote.assert_managed(worker)
                await self.remote.verify_rpc(worker)
        except Exception:
            await self.store.claim_state(instance_id, "quarantined")
        else:
            await self.store.claim_state(instance_id, "released")

    async def release(self, instance_id):
        async with self.remote.operations:
            await self._release_locked(instance_id)

    async def active(self, instance_id):
        await self.store.claim_state(instance_id, "active")

    async def healthy(self, record):
        info = record.get("runtime", {}).get("placement")
        if not info:
            return True
        for wid, expected in info["worker_versions"].items():
            worker = self.remote.workers.get(wid)
            if not worker:
                return False
            node = self.remote.nodes.get(worker["node_id"])
            # SSH loss alone is not evidence that the live RPC connection failed.
            if not node or node["connection_state"] != "online":
                continue
            if worker.get("process_identity") != expected["process_identity"] or worker[
                "state"
            ] in {"failed", "stopped", "uninstalled"}:
                return False
        return True

    async def reconcile(self, identifier):
        worker = self.remote.worker(identifier)

        async def work(job):
            async with self.remote.operations:
                claims = [
                    c for c in await self.store.claims() if c["worker_id"] == identifier
                ]
                for claim in claims:
                    record = (
                        self.instance_manager.instances.get(claim["instance_id"])
                        if self.instance_manager
                        else None
                    )
                    if record and self.instance_manager.adapters[
                        record["runtime_type"]
                    ].get_status(record):
                        raise ManagerError(
                            "worker_busy", "所属 llama-server 仍运行，不能解除占用"
                        )
                    await self._release_locked(claim["instance_id"])
                remaining = [
                    c for c in await self.store.claims() if c["worker_id"] == identifier
                ]
                return {"claims": remaining, "released": not remaining}

        return await self.remote.jobs.submit(
            worker["node_id"], "reconcile", work, worker_id=identifier
        )
