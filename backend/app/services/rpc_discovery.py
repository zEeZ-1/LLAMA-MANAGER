import re
import copy
import uuid

from ..core.errors import ManagerError
from ..nodes.telemetry import now
from ..remote import rpc_scripts
from ..remote.discovery import parse_discovery


class RpcDiscovery:
    def __init__(self, remote):
        self.remote = remote

    async def scan(self, node, *, verify=True):
        remote = self.remote
        text = await remote.ssh.script(node, rpc_scripts.DISCOVER, retry=True)
        found = parse_discovery(text, node["address"])
        claims = {c["worker_id"]: c for c in await remote.store.claims()}
        records = remote.releases.records()
        seen = set()
        for process in found["processes"]:
            worker = next(
                (
                    w
                    for w in remote.node_workers(node["id"])
                    if w["endpoint"]["port"] == process["port"]
                ),
                None,
            )
            if worker is None:
                wid = str(uuid.uuid4())
                worker = dict(
                    id=wid,
                    node_id=node["id"],
                    device_ids=[],
                    endpoint={"host": node["address"], "port": process["port"]},
                    management_mode="observed",
                    controller_id=None,
                    service_name=None,
                    state="unknown",
                    release_id=None,
                    protocol_version=None,
                    runtime_commit=None,
                    process_identity=None,
                    autostart=False,
                    origin="existing",
                    deleted_at=None,
                )
                remote.workers[wid] = worker
            seen.add(worker["id"])
            old_identity = worker.get("process_identity")
            owned_package = bool(
                worker.get("release_id")
                and not worker.get("imported")
                and worker.get("root")
            )
            if not owned_package:
                worker["origin"] = "existing"
                worker["imported"] = True
                worker["management_mode"] = "observed"
                worker["controller_id"] = None
            identity_changed = old_identity != process["process_identity"]
            if identity_changed:
                worker["protocol_version"] = None
                worker["last_compute_check_at"] = None
            worker.update(
                process_identity=process["process_identity"],
                rss_bytes=process["rss_bytes"],
                client_count=process["client_count"],
                bind_address=process["bind_address"],
                device_names=process["device_names"],
                owned_by_login=process["owned_by_login"],
                service_scope=process["service_scope"],
                discovered_at=now(),
                reachable=process["reachable"],
                can_delete=process["owned_by_login"]
                and process["service_scope"] != "system",
            )
            if not owned_package:
                worker["service_name"] = (
                    process["service_name"] if process["owned_by_login"] else None
                )
                worker["executable_path"] = process["executable_path"]
                worker["expected_binary_sha256"] = process["binary_sha256"]
            compatible = next(
                (
                    r
                    for r in reversed(records)
                    if r.get("files", {}).get("bin/ggml-rpc-server")
                    == process["binary_sha256"]
                    and r.get("commit") == remote.cfg.rpc.expected_commit
                    and r.get("protocol_version") == remote.cfg.rpc.protocol_version
                ),
                None,
            )
            devices = node.get("snapshot", {}).get("devices", [])
            names = process["device_names"]
            if names:
                mapping = {"CUDA" + str(d["backend_index"]): d["id"] for d in devices}
                selected = [mapping[n] for n in names if n in mapping]
                device_match = len(selected) == len(names)
            else:
                selected = [d["id"] for d in devices]
                device_match = bool(selected)
            if not owned_package:
                worker["device_ids"] = selected
                if not compatible:
                    worker.update(
                        release_id=None, runtime_commit=None, protocol_version=None
                    )
            status = None
            if (
                process["service_scope"] == "user"
                and process["service_name"]
                and process["owned_by_login"]
            ):
                status = await remote.status(worker)
                if (
                    str(status.get("MainPID"))
                    != str(process["process_identity"]["pid"])
                    or status.get("ExecutableSHA256") != process["binary_sha256"]
                ):
                    worker["state"] = "unknown"
                    worker.update(
                        usable=False,
                        can_delete=False,
                        can_start=False,
                        protocol_version=None,
                    )
                    worker["last_error"] = {
                        "code": "service_identity_changed",
                        "message": "RPC 服务进程发生变化，请重新检测",
                    }
                    await remote.store.put("workers", worker)
                    continue
                if not owned_package:
                    worker["service_signature"] = status.get("ExecStart")
            worker["can_start"] = bool(worker.get("service_name"))
            worker["usable"] = False
            worker["last_error"] = None
            worker["state"] = "unverified"
            if not process["owned_by_login"]:
                worker["last_error"] = {
                    "code": "rpc_permission_required",
                    "message": "RPC 属于其他账户，无法确认占用和服务权限，请使用对应 SSH 账户",
                }
            elif not process["reachable"]:
                worker["last_error"] = {
                    "code": "rpc_not_reachable",
                    "message": "RPC 仅监听本机或其他地址，控制主机无法访问",
                }
            elif not compatible:
                worker["last_error"] = {
                    "code": "version_unverified",
                    "message": "已发现 RPC，但程序版本未与控制端发布包匹配",
                }
            elif not device_match:
                worker["last_error"] = {
                    "code": "device_mismatch",
                    "message": "RPC 的设备选择与节点 GPU 不匹配",
                }
            else:
                if not owned_package:
                    worker.update(
                        release_id=compatible["id"],
                        runtime_commit=compatible["commit"],
                        management_mode="managed",
                        controller_id=remote.store.controller_id,
                    )
                if process["client_count"] and worker["id"] not in claims:
                    worker["state"] = "busy"
                    worker["last_error"] = {
                        "code": "rpc_in_use",
                        "message": "RPC 已有客户端连接，空闲后会自动验证",
                    }
                elif worker["id"] in claims:
                    worker["state"] = "failed" if identity_changed else "ready"
                    worker["usable"] = not identity_changed
                elif verify:
                    try:
                        await remote.verify_rpc(worker)
                        worker["usable"] = True
                    except ManagerError as exc:
                        worker["state"] = "unverified"
                        worker["last_error"] = exc.detail
                elif worker.get("protocol_version"):
                    worker["state"] = "ready"
                    worker["usable"] = True
            await remote.store.put("workers", worker)
        for worker in remote.node_workers(node["id"]):
            if worker["id"] in seen:
                continue
            if worker.get("origin") == "existing":
                if worker.get("service_name"):
                    await remote.refresh_worker(worker)
                else:
                    worker.update(
                        state="stopped",
                        process_identity=None,
                        protocol_version=None,
                        rss_bytes=0,
                        client_count=0,
                        usable=False,
                    )
                    await remote.store.put("workers", worker)
            elif worker.get("service_name"):
                await remote.refresh_worker(worker)
        ports = set(found["listening_ports"])
        ports.update(w["endpoint"]["port"] for w in remote.node_workers(node["id"]))
        suggested = next(
            (
                p
                for p in range(
                    remote.cfg.rpc.default_port,
                    min(65536, remote.cfg.rpc.default_port + 100),
                )
                if p not in ports
            ),
            None,
        )
        node["rpc_discovery"] = {
            "state": "complete",
            "sampled_at": now(),
            "count": len(found["processes"]),
            "suggested_port": suggested,
            "listening_ports": found["listening_ports"],
            "error": None,
        }
        await remote.save_node(node)
        return remote.public_node(node)

    async def safe_scan(self, node):
        try:
            return await self.scan(node)
        except Exception as exc:
            node["rpc_discovery"] = {
                "state": "failed",
                "sampled_at": now(),
                "error": getattr(
                    exc,
                    "detail",
                    {"code": "discovery_failed", "message": "RPC 检测失败，请刷新重试"},
                ),
            }
            await self.remote.save_node(node)
            return self.remote.public_node(node)

    async def remove(self, identifier, body):
        remote = self.remote
        worker = remote.worker(identifier)
        await remote.assert_idle([identifier])
        confirmed_identity = copy.deepcopy(
            body.process_identity
            if "process_identity" in body.model_fields_set
            else worker.get("process_identity")
        )

        async def work(job):
            async with remote.operations:
                worker = remote.worker(identifier)
                await remote.assert_idle([identifier])
                node = remote.node(worker["node_id"])
                await remote.jobs.phase(job, "removing", "正在核对 RPC 身份并删除服务")
                await remote.collect(node)
                if confirmed_identity != worker.get("process_identity"):
                    raise ManagerError(
                        "rpc_identity_changed",
                        "页面中的 RPC 进程已变化，请刷新后重新确认",
                    )
                if worker.get("origin") == "existing" or worker.get("release_id"):
                    # Never open a probe connection during deletion.
                    await self.scan(node, verify=False)
                    if confirmed_identity != worker.get("process_identity"):
                        raise ManagerError(
                            "rpc_identity_changed", "RPC 进程已变化，未执行删除"
                        )
                    if worker.get("client_count") and not body.force:
                        raise ManagerError(
                            "rpc_in_use",
                            "RPC 仍有客户端连接，请先断开或明确确认强制删除",
                        )
                if worker.get("origin") == "existing":
                    if worker.get("process_identity") and not worker.get(
                        "owned_by_login"
                    ):
                        raise ManagerError(
                            "rpc_permission_required",
                            "当前 SSH 账户无权删除其他账户的 RPC",
                        )
                    if worker.get("service_scope") == "system":
                        raise ManagerError(
                            "rpc_permission_required",
                            "这是系统级 RPC 服务，请由管理员停止或卸载",
                        )
                    identity = worker.get("process_identity")
                    if identity:
                        try:
                            await remote.ssh.script(
                                node,
                                rpc_scripts.REMOVE_EXTERNAL,
                                [
                                    identity["pid"],
                                    identity["start_time"],
                                    identity["boot_id"],
                                    worker["expected_binary_sha256"],
                                    worker.get("service_name") or "",
                                    worker.get("service_signature") or "",
                                    "yes" if body.force else "no",
                                ],
                                timeout=25,
                            )
                        except ManagerError as exc:
                            for code, message in [
                                (
                                    "rpc_identity_changed",
                                    "RPC 身份发生变化，未执行删除",
                                ),
                                ("rpc_in_use", "RPC 出现新的客户端连接，未执行删除"),
                                (
                                    "rpc_permission_required",
                                    "当前 SSH 账户没有删除权限",
                                ),
                            ]:
                                if code in exc.detail.get("message", ""):
                                    raise ManagerError(code, message) from exc
                            raise
                    elif worker.get("service_name"):
                        status = await remote.status(worker)
                        if int(status.get("MainPID") or 0):
                            raise ManagerError(
                                "rpc_identity_changed", "服务已经重新启动，请刷新后确认"
                            )
                        if status.get("LoadState") != "not-found":
                            if status.get("ExecStart") != worker.get(
                                "service_signature"
                            ):
                                raise ManagerError(
                                    "rpc_identity_changed", "服务配置已变化"
                                )
                            await remote.ssh.script(
                                node,
                                rpc_scripts.REMOVE_EXTERNAL,
                                [
                                    0,
                                    "",
                                    "",
                                    "",
                                    worker["service_name"],
                                    worker["service_signature"],
                                    "no",
                                ],
                                timeout=25,
                            )
                elif worker.get("release_id"):
                    await remote.assert_managed(worker)
                    if not re.fullmatch(
                        r"\.local/share/llama-manager/[a-f0-9-]+/[a-f0-9-]+",
                        worker["root"],
                    ):
                        raise ManagerError(
                            "worker_identity_invalid", "RPC 专属目录不合法"
                        )
                    await remote.ssh.script(
                        node,
                        rpc_scripts.UNINSTALL,
                        [worker["root"], worker["service_name"], worker["owner"]],
                        timeout=30,
                    )
                worker.update(
                    deleted_at=now(),
                    state="deleted",
                    process_identity=None,
                    protocol_version=None,
                    usable=False,
                )
                await remote.store.put("workers", worker)
                await self.safe_scan(node)
                return {
                    "id": identifier,
                    "status": "deleted",
                    "external_files_preserved": worker.get("origin") == "existing",
                }

        return await remote.jobs.submit(
            worker["node_id"], "delete_rpc", work, worker_id=identifier
        )
