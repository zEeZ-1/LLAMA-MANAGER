import asyncio
import copy
import ipaddress
import shlex
import time
import uuid
from ..core.errors import ManagerError
from ..nodes.telemetry import now, parse_inventory, public_snapshot, snapshot
from ..remote import scripts
from ..remote.releases import ReleaseCatalog
from ..runtime.rpc_probe import probe
from .node_manager import INVENTORY_SCRIPT


class RemoteManager:
    def __init__(self, cfg, store, transport, credentials, jobs):
        self.cfg, self.store, self.ssh, self.credentials, self.jobs = (
            cfg,
            store,
            transport,
            credentials,
            jobs,
        )
        self.nodes = {}
        self.workers = {}
        self.challenges = {}
        self.operations = asyncio.Lock()
        self.task = None
        self.releases = ReleaseCatalog(cfg)
        self.local_provider = None
        self.retry = {}
        self.inventory_sampled = {}

    async def initialize(self):
        self.nodes = {r["id"]: r for r in await self.store.all("nodes")}
        self.workers = {r["id"]: r for r in await self.store.all("workers")}
        for node in self.nodes.values():
            if node.get("snapshot"):
                node["snapshot"]["_sample_monotonic"] = 0
        self.task = asyncio.create_task(self._poll())

    def node(self, identifier):
        node = self.nodes.get(identifier)
        if not node or node.get("archived_at"):
            raise ManagerError("node_missing", "节点不存在", 404)
        return node

    def worker(self, identifier):
        worker = self.workers.get(identifier)
        if not worker:
            raise ManagerError("worker_missing", "RPC worker 不存在", 404)
        self.node(worker["node_id"])
        return worker

    def public_worker(self, worker):
        return {
            k: copy.deepcopy(v)
            for k, v in worker.items()
            if k
            not in {
                "root",
                "owner",
                "expected_binary_sha256",
                "service_signature",
                "expected_unit_sha256",
                "expected_runner_sha256",
                "executable_path",
            }
        }

    def public_node(self, node):
        data = public_snapshot(
            node.get("snapshot", {}), self.cfg.nodes.telemetry_stale_after_seconds
        )
        data.update(
            {
                k: copy.deepcopy(node[k])
                for k in (
                    "id",
                    "name",
                    "address",
                    "ssh",
                    "connection_state",
                    "created_at",
                    "updated_at",
                    "identity",
                    "archived_at",
                )
            }
        )
        data.update(
            status="online" if node["connection_state"] == "online" else "offline",
            role="rpc_worker",
            node_type="ssh",
            model_count=0,
            instance_count=0,
            ready_count=0,
            manager_memory_bytes=None,
            rpc_workers=[
                self.public_worker(w)
                for w in self.workers.values()
                if w["node_id"] == node["id"]
            ],
            last_error=node.get("last_error"),
        )
        data.setdefault("hostname", node["address"])
        data.setdefault("os", "尚未采集")
        data.setdefault("architecture", "尚未采集")
        data.setdefault(
            "cpu_info",
            {"name": "尚未采集", "logical_count": None, "usage_percent": None},
        )
        data.setdefault(
            "memory_info",
            {"total": None, "available": None, "used": None, "usage_percent": None},
        )
        data.setdefault("devices", [])
        return data

    def validate_address(self, address):
        value = ipaddress.ip_address(address)
        if value.version != 4 or not any(
            value in ipaddress.ip_network(net)
            for net in self.cfg.nodes.allowed_networks
        ):
            raise ManagerError("address_not_allowed", "地址不在允许的节点网段中", 422)

    async def add_node(self, body):
        async with self.operations:
            self.validate_address(str(body.address))
            if (
                len([n for n in self.nodes.values() if not n.get("archived_at")])
                >= self.cfg.nodes.max_registered
            ):
                raise ManagerError("node_limit", "登记节点数量已达到上限")
            if any(
                n["address"] == str(body.address) and not n.get("archived_at")
                for n in self.nodes.values()
            ):
                raise ManagerError("node_exists", "这个 IP 已登记，请编辑已有节点")
            if body.credential_id:
                self.credentials.resolve(body.credential_id)
            node = dict(
                id=str(uuid.uuid4()),
                name=body.name.strip(),
                address=str(body.address),
                ssh={
                    "port": body.port,
                    "username": body.username,
                    "credential_id": body.credential_id,
                },
                connection_state="unknown",
                identity={"machine_id_hash": None, "ssh_host_key_fingerprint": None},
                snapshot={},
                archived_at=None,
                last_error=None,
                created_at=now(),
                updated_at=now(),
            )
            self.nodes[node["id"]] = node
            await self.save_node(node)
            return self.public_node(node)

    async def save_node(self, node):
        node["updated_at"] = now()
        await self.store.put("nodes", node)

    async def assert_idle(self, worker_ids):
        busy = [c for c in await self.store.claims() if c["worker_id"] in worker_ids]
        if busy:
            raise ManagerError(
                "worker_busy",
                f"Worker 被实例 {busy[0]['instance_id']} 占用（{busy[0]['state']}）",
            )

    async def edit_node(self, identifier, body):
        async with self.operations:
            node = self.node(identifier)
            ids = [w["id"] for w in self.workers.values() if w["node_id"] == identifier]
            await self.assert_idle(ids)
            changes = body.model_dump(exclude_unset=True, exclude_none=True)
            target_changed = any(
                k in changes for k in ("address", "port", "username", "credential_id")
            )
            if target_changed and any(
                self.workers[i]["state"] not in {"stopped", "uninstalled"} for i in ids
            ):
                raise ManagerError(
                    "worker_running", "请先停止此节点的 RPC 服务，再修改连接配置"
                )
            if "address" in changes:
                self.validate_address(str(changes["address"]))
                if any(
                    n["id"] != identifier
                    and n["address"] == str(changes["address"])
                    and not n.get("archived_at")
                    for n in self.nodes.values()
                ):
                    raise ManagerError("node_exists", "新 IP 已登记")
                node["address"] = str(changes.pop("address"))
                for wid in ids:
                    worker = self.workers[wid]
                    worker["endpoint"]["host"] = node["address"]
                    worker["process_identity"] = None
                    worker["protocol_version"] = None
                    await self.store.put("workers", worker)
            if "name" in changes:
                node["name"] = changes.pop("name").strip()
            if changes.get("credential_id"):
                self.credentials.resolve(changes["credential_id"])
            node["ssh"].update(changes)
            if target_changed:
                await self.ssh.close_node(identifier)
                node["connection_state"] = "unknown"
            await self.save_node(node)
            return self.public_node(node)

    async def archive_node(self, identifier):
        async with self.operations:
            node = self.node(identifier)
            await self.assert_idle(
                [w["id"] for w in self.workers.values() if w["node_id"] == identifier]
            )
            if any(
                w["node_id"] == identifier
                and w["state"] not in {"stopped", "uninstalled"}
                for w in self.workers.values()
            ):
                raise ManagerError("worker_running", "先停止并核实所属 RPC，再移除节点")
            await self.ssh.close_node(identifier)
            node["archived_at"] = now()
            await self.save_node(node)
            return {"status": "archived"}

    async def scan_host(self, identifier):
        node = self.node(identifier)
        keys = await self.ssh.scan(node)
        challenge = str(uuid.uuid4())
        self.challenges = {
            k: v for k, v in self.challenges.items() if v["expires"] > time.monotonic()
        }
        self.challenges[challenge] = {
            "node_id": identifier,
            "address": node["address"],
            "port": node["ssh"]["port"],
            "keys": keys,
            "expires": time.monotonic() + 300,
        }
        return {
            "challenge_id": challenge,
            "expires_in_seconds": 300,
            "keys": [{k: v for k, v in key.items() if k != "key"} for key in keys],
            "current_fingerprint": node["identity"]["ssh_host_key_fingerprint"],
        }

    async def trust_host(self, identifier, body):
        async with self.operations:
            node = self.node(identifier)
            value = self.challenges.pop(body.challenge_id, None)
            if (
                not value
                or value["node_id"] != identifier
                or value["address"] != node["address"]
                or value["port"] != node["ssh"]["port"]
                or value["expires"] < time.monotonic()
            ):
                raise ManagerError(
                    "challenge_expired", "主机指纹核验已过期或连接目标发生变化"
                )
            key = next(
                (k for k in value["keys"] if k["fingerprint"] == body.fingerprint), None
            )
            if not key:
                raise ManagerError("fingerprint_invalid", "指纹不属于本次扫描结果")
            old = node["identity"]["ssh_host_key_fingerprint"]
            if old and old != body.fingerprint and not body.replace:
                raise ManagerError("host_key_changed", "主机密钥变化，必须明确确认替换")
            if old and old != body.fingerprint:
                await self.assert_idle(
                    [
                        w["id"]
                        for w in self.workers.values()
                        if w["node_id"] == identifier
                    ]
                )
            await self.ssh.close_node(identifier)
            path = self.ssh.known_hosts(node)
            path.write_text(
                f"{self.ssh.host_pattern(node)} {key['algorithm']} {key['key']}\n"
            )
            path.chmod(0o600)
            node["identity"]["ssh_host_key_fingerprint"] = body.fingerprint
            node["connection_state"] = "unknown"
            await self.save_node(node)
            return self.public_node(node)

    async def collect(self, node, password=None, *, full=True):
        try:
            full = (
                full
                or time.monotonic() - self.inventory_sampled.get(node["id"], 0)
                > self.cfg.nodes.inventory_ttl_seconds
            )
            text = await self.ssh.script(
                node,
                INVENTORY_SCRIPT.read_text(),
                ["full" if full else "dynamic"],
                password=password,
            )
            data = snapshot(parse_inventory(text), node["id"], node.get("snapshot"))
            machine = data["inventory"]["machine_id_hash"]
            previous = node["identity"].get("machine_id_hash")
            if previous and machine and previous != machine:
                raise ManagerError(
                    "host_identity_changed",
                    "远端系统身份发生变化，请重新登记并核验机器",
                )
            node["identity"]["machine_id_hash"] = machine
            node.update(snapshot=data, connection_state="online", last_error=None)
            if full:
                self.inventory_sampled[node["id"]] = time.monotonic()
            await self.save_node(node)
            return data
        except Exception as exc:
            detail = getattr(exc, "detail", {})
            code = (
                detail.get("code", "ssh_unreachable")
                if isinstance(detail, dict)
                else "ssh_unreachable"
            )
            node["connection_state"] = (
                code
                if code in {"auth_failed", "host_key_changed", "host_key_untrusted"}
                else "offline"
            )
            node["last_error"] = {
                "code": code,
                "message": str(detail.get("message", "节点采集失败"))
                if isinstance(detail, dict)
                else "节点采集失败",
            }
            await self.save_node(node)
            raise

    async def connect(self, identifier, password=None):
        created = time.monotonic()

        async def work(job):
            async with self.operations:
                if time.monotonic() - created > self.cfg.jobs.credential_ttl_seconds:
                    raise ManagerError(
                        "credential_expired", "一次性认证信息已过期，请重新输入"
                    )
                node = self.node(identifier)
                await self.jobs.phase(job, "authenticating", "正在认证并读取机器配置")
                if password is not None:
                    await self.ssh.close_node(identifier)
                await self.collect(node, password)
                if password is not None:
                    await self.jobs.phase(
                        job, "installing_key", "正在安装本控制端专用公钥，密码不保存"
                    )
                    credential = await self.credentials.generated()
                    public = await self.credentials.public_key(credential["id"])
                    await self.ssh.script(node, scripts.INSTALL_KEY, [public])
                    node["ssh"]["credential_id"] = credential["id"]
                    await self.save_node(node)
                    await self.ssh.close_node(identifier)
                    await self.jobs.phase(job, "verifying_key", "正在验证后续密钥连接")
                    await self.collect(node)
                for worker in self.workers.values():
                    if worker["node_id"] == identifier and worker.get("service_name"):
                        await self.refresh_worker(worker)
                return self.public_node(node)

        return await self.jobs.submit(
            identifier, "connect", work, secrets=[password] if password else []
        )

    async def refresh(self, identifier):
        async def work(job):
            async with self.operations:
                node = self.node(identifier)
                await self.collect(node)
                for worker in self.workers.values():
                    if worker["node_id"] == identifier and worker.get("service_name"):
                        await self.refresh_worker(worker)
                return self.public_node(node)

        self.node(identifier)
        return await self.jobs.submit(identifier, "refresh", work)

    async def add_worker(self, identifier, body):
        async with self.operations:
            node = self.node(identifier)
            if any(w["node_id"] == identifier for w in self.workers.values()):
                raise ManagerError("worker_exists", "V2 每个节点登记一个 RPC worker")
            available = {d["id"] for d in node.get("snapshot", {}).get("devices", [])}
            if not body.device_ids or not set(body.device_ids) <= available:
                raise ManagerError(
                    "device_unavailable", "请先连接节点并选择识别出的设备"
                )
            wid = str(uuid.uuid4())
            worker = dict(
                id=wid,
                node_id=identifier,
                device_ids=body.device_ids,
                endpoint={
                    "host": node["address"],
                    "port": body.port or self.cfg.rpc.default_port,
                },
                management_mode="observed" if body.mode == "observed" else "managed",
                controller_id=self.store.controller_id
                if body.mode == "managed"
                else None,
                service_name=body.service_name
                if body.mode == "observed"
                else f"llama-manager-rpc-{wid[:12]}.service",
                state="unknown" if body.mode == "observed" else "uninstalled",
                root=f".local/share/llama-manager/{self.store.controller_id}/{wid}",
                owner=f"{self.store.controller_id}:{wid}",
                process_identity=None,
                last_error=None,
                release_id=None,
                protocol_version=None,
                runtime_commit=None,
                expected_binary_sha256=None,
                autostart=False,
            )
            self.workers[wid] = worker
            await self.store.put("workers", worker)
            return self.public_worker(worker)

    async def status(self, worker):
        if not worker.get("service_name"):
            return {"LoadState": "not-found", "MainPID": "0", "ActiveState": "inactive"}
        text = await self.ssh.script(
            self.node(worker["node_id"]),
            scripts.SERVICE_STATUS,
            [worker["service_name"]],
        )
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    async def edit_worker(self, identifier, body):
        async with self.operations:
            worker = self.worker(identifier)
            await self.assert_idle([identifier])
            if worker.get("release_id") or worker.get("imported"):
                raise ManagerError(
                    "worker_config_locked", "已部署或接管的 RPC 不能直接修改连接配置"
                )
            node = self.node(worker["node_id"])
            available = {d["id"] for d in node.get("snapshot", {}).get("devices", [])}
            if not set(body.device_ids) <= available:
                raise ManagerError(
                    "device_unavailable", "选择的设备已不存在，请刷新节点"
                )
            worker.update(
                device_ids=body.device_ids,
                endpoint={
                    "host": node["address"],
                    "port": body.port or self.cfg.rpc.default_port,
                },
                management_mode=body.mode,
                controller_id=self.store.controller_id
                if body.mode == "managed"
                else None,
                service_name=body.service_name
                if body.mode == "observed"
                else f"llama-manager-rpc-{identifier[:12]}.service",
                state="unknown" if body.mode == "observed" else "uninstalled",
                process_identity=None,
                protocol_version=None,
                last_error=None,
            )
            await self.store.put("workers", worker)
            return self.public_worker(worker)

    async def refresh_worker(self, worker):
        data = await self.status(worker)
        pid = int(data.get("MainPID", "0"))
        old = worker.get("process_identity")
        identity = (
            {
                "pid": pid,
                "start_time": data.get("ProcessStart"),
                "boot_id": data.get("BootID"),
            }
            if pid
            else None
        )
        if old != identity:
            worker["protocol_version"] = None
        worker["state"] = (
            ("ready" if worker.get("protocol_version") else "starting")
            if data.get("ActiveState") == "active" and pid
            else (
                "failed"
                if data.get("ActiveState") == "failed"
                else (
                    "uninstalled" if data.get("LoadState") == "not-found" else "stopped"
                )
            )
        )
        if (
            worker.get("expected_binary_sha256")
            and pid
            and data.get("ExecutableSHA256") != worker["expected_binary_sha256"]
        ):
            worker["state"] = "failed"
            worker["last_error"] = {
                "code": "service_identity_changed",
                "message": "运行中的 RPC 二进制与已验证发布包不匹配",
            }
        worker["process_identity"] = identity
        worker["rss_bytes"] = int(data.get("RSSBytes") or 0) if pid else 0
        if (
            old
            and identity
            and old != identity
            and any(c["worker_id"] == worker["id"] for c in await self.store.claims())
        ):
            worker["state"] = "failed"
            worker["last_error"] = {
                "code": "worker_restarted",
                "message": "占用中的 RPC 进程身份发生变化",
            }
        await self.store.put("workers", worker)
        return data

    async def assert_managed(self, worker):
        if (
            worker["management_mode"] != "managed"
            or worker["controller_id"] != self.store.controller_id
        ):
            raise ManagerError(
                "worker_not_managed", "此 RPC 尚未验证接管，不能启停或修改"
            )
        node = self.node(worker["node_id"])
        if worker.get("imported"):
            data = await self.status(worker)
            if data.get("ExecStart") != worker.get("service_signature"):
                raise ManagerError(
                    "service_identity_changed", "关联服务的启动配置发生变化"
                )
        else:
            checked = await self.ssh.script(
                node,
                scripts.CHECK_OWNER,
                [worker["root"], worker["owner"], worker["service_name"]],
            )
            signatures = dict(
                line.split("=", 1) for line in checked.splitlines() if "=" in line
            )
            if (
                not worker.get("expected_unit_sha256")
                or signatures.get("UnitSHA256") != worker["expected_unit_sha256"]
                or signatures.get("RunnerSHA256")
                != worker.get("expected_runner_sha256")
            ):
                raise ManagerError(
                    "service_identity_changed",
                    "所属服务或启动脚本摘要发生变化，拒绝启停",
                )
        if worker.get("imported") and worker.get("executable_path"):
            actual = await self.ssh.execute(
                node, "sha256sum -- " + shlex.quote(worker["executable_path"])
            )
            if actual.split()[0] != worker.get("expected_binary_sha256"):
                raise ManagerError(
                    "service_identity_changed", "关联 RPC 文件发生变化，拒绝启动"
                )
        data = await self.status(worker)
        if int(data.get("MainPID", "0")) and data.get("ExecutableSHA256") != worker.get(
            "expected_binary_sha256"
        ):
            raise ManagerError(
                "service_identity_changed", "拒绝操作二进制身份不匹配的服务"
            )
        return data

    async def verify_rpc(self, worker):
        result = await probe(
            worker["endpoint"]["host"],
            worker["endpoint"]["port"],
            self.cfg.rpc.probe_timeout_seconds,
            self.cfg.rpc.protocol_version,
        )
        if len(result["devices"]) != len(worker["device_ids"]):
            raise ManagerError("device_mismatch", "RPC 暴露的设备数与登记配置不同")
        worker["protocol_version"] = result["protocol_version"]
        worker["rpc_devices"] = result["devices"]
        worker["last_compute_check_at"] = now()
        worker["last_error"] = None
        worker["state"] = "ready"
        await self.store.put("workers", worker)
        return result

    async def start_unlocked(self, worker):
        await self.assert_managed(worker)
        node = self.node(worker["node_id"])
        await self.ssh.execute(
            node, "systemctl --user start " + shlex.quote(worker["service_name"])
        )
        deadline = time.monotonic() + self.cfg.rpc.startup_timeout_seconds
        while time.monotonic() < deadline:
            data = await self.refresh_worker(worker)
            if worker["state"] == "failed":
                raise ManagerError("rpc_start_failed", "RPC 服务启动失败，请读取日志")
            if worker["state"] in {"ready", "starting"} and int(
                data.get("MainPID", "0")
            ):
                try:
                    await self.verify_rpc(worker)
                    return self.public_worker(worker)
                except ManagerError as exc:
                    if exc.code not in {"rpc_unreachable"}:
                        raise
            await asyncio.sleep(0.3)
        raise ManagerError("rpc_start_timeout", "等待 RPC 启动超时")

    async def operation(self, identifier, operation, idempotency_key=None):
        worker = self.worker(identifier)

        async def work(job):
            async with self.operations:
                await self.assert_idle([identifier])
                await self.jobs.phase(
                    job,
                    operation,
                    "正在"
                    + {"start": "启动 RPC", "stop": "停止 RPC", "clean": "清理缓存"}[
                        operation
                    ],
                )
                await self.assert_managed(worker)
                node = self.node(worker["node_id"])
                if operation == "start":
                    return await self.start_unlocked(worker)
                if operation == "stop":
                    await self.ssh.execute(
                        node,
                        "systemctl --user stop " + shlex.quote(worker["service_name"]),
                        timeout=20,
                    )
                    await self.refresh_worker(worker)
                    if worker["state"] != "stopped":
                        raise ManagerError("rpc_stop_failed", "RPC 未确认停止")
                elif operation == "clean":
                    await self.refresh_worker(worker)
                    if worker["state"] != "stopped":
                        raise ManagerError(
                            "worker_running", "仅可在 RPC 停止后清理缓存"
                        )
                    if worker.get("imported"):
                        raise ManagerError(
                            "cache_not_owned", "不清理外部关联服务的缓存"
                        )
                    await self.ssh.script(
                        node, scripts.CLEAN_CACHE, [worker["root"], worker["owner"]]
                    )
                return self.public_worker(worker)

        await self.assert_idle([identifier])
        return await self.jobs.submit(
            worker["node_id"], operation, work, idempotency_key=idempotency_key
        )

    async def logs(self, identifier):
        worker = self.worker(identifier)
        if not worker.get("service_name"):
            raise ManagerError("service_missing", "未关联 RPC 服务")
        text = await self.ssh.execute(
            self.node(worker["node_id"]),
            "journalctl --user -u "
            + shlex.quote(worker["service_name"])
            + " --no-pager -n 160 -o cat | tail -c 32768",
        )
        return {"text": text}

    async def _poll(self):
        while True:
            await asyncio.sleep(self.cfg.nodes.telemetry_interval_seconds)
            await self.ssh.reap_idle()
            for node in list(self.nodes.values()):
                if (
                    node.get("archived_at")
                    or not node["identity"].get("machine_id_hash")
                    or self.operations.locked()
                    or self.retry.get(node["id"], (0, 0))[1] > time.monotonic()
                ):
                    continue
                try:
                    async with self.operations:
                        await self.collect(node, full=False)
                        for worker in self.workers.values():
                            if worker["node_id"] == node["id"] and worker.get(
                                "service_name"
                            ):
                                await self.refresh_worker(worker)
                        self.retry.pop(node["id"], None)
                except Exception:
                    failures = min(self.retry.get(node["id"], (0, 0))[0] + 1, 8)
                    self.retry[node["id"]] = (
                        failures,
                        time.monotonic()
                        + min(
                            300, self.cfg.nodes.telemetry_interval_seconds * 2**failures
                        ),
                    )

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
