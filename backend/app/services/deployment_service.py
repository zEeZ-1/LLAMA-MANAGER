import asyncio
import shlex
import time
from ..core.errors import ManagerError
from ..remote import scripts


class DeploymentService:
    def __init__(self, remote):
        self.remote = remote
        self.slot = asyncio.Semaphore(remote.cfg.jobs.max_concurrent_deployments)

    async def deploy(self, identifier, body):
        remote = self.remote
        worker = remote.worker(identifier)
        secret = body.sudo_password.get_secret_value() if body.sudo_password else None
        created = time.monotonic()

        async def work(job):
            async with self.slot, remote.operations:
                await remote.assert_idle([identifier])
                if worker.get("imported") or worker["management_mode"] != "managed":
                    raise ManagerError(
                        "worker_not_managed",
                        "不能覆盖外部关联 RPC；请登记平台专用 worker",
                    )
                node = remote.node(worker["node_id"])
                await remote.jobs.phase(job, "inspecting", "正在核对环境与发布包")
                await remote.collect(node)
                release = await remote.releases.get(body.release_id, node)
                if node["snapshot"]["inventory"].get("linger") != "yes":
                    if not body.enable_linger:
                        raise ManagerError(
                            "privilege_required",
                            "需要启用此账户 linger；请在部署表单授权，或由管理员提前启用",
                        )
                    await remote.jobs.phase(
                        job, "linger", "正在启用此账户的用户服务持续运行"
                    )
                    if secret is not None:
                        if (
                            time.monotonic() - created
                            > remote.cfg.jobs.credential_ttl_seconds
                        ):
                            raise ManagerError("credential_expired", "管理员凭据已过期")
                        command = 'sudo -S -p "" loginctl enable-linger ' + shlex.quote(
                            node["ssh"]["username"]
                        )
                        try:
                            await remote.ssh.execute(
                                node, command, stdin=(secret + "\n").encode()
                            )
                        except Exception as exc:
                            raise ManagerError(
                                "privilege_required",
                                "启用 linger 失败，请核对管理员权限",
                            ) from exc
                    else:
                        try:
                            await remote.ssh.execute(
                                node,
                                "sudo -n loginctl enable-linger "
                                + shlex.quote(node["ssh"]["username"]),
                            )
                        except Exception as exc:
                            raise ManagerError(
                                "privilege_required",
                                "需要管理员认证以启用 linger；未修改 sudoers",
                            ) from exc
                    await remote.collect(node)
                    if node["snapshot"]["inventory"].get("linger") != "yes":
                        raise ManagerError(
                            "privilege_required", "未确认 linger 已启用，停止部署"
                        )
                existing = await remote.status(worker)
                if existing.get("LoadState") != "not-found" and not worker.get(
                    "release_id"
                ):
                    raise ManagerError(
                        "service_conflict", "已有同名服务不属于已验证发布，拒绝覆盖"
                    )
                if worker.get("release_id"):
                    await remote.assert_managed(worker)
                # A port can be occupied by an unrelated service. Never stop it to make room.
                port = worker["endpoint"]["port"]
                command = f"ss -ltnH '( sport = :{port} )'"
                listeners = await remote.ssh.execute(node, command)
                if listeners.strip() and not worker.get("release_id"):
                    raise ManagerError(
                        "port_conflict",
                        f"{worker['endpoint']['host']}:{port} 已被现有进程占用，请修改 RPC 配置换用空闲端口，或关联已有服务后验证接管",
                    )
                await remote.jobs.phase(job, "preparing", "正在准备所属目录和磁盘空间")
                prepared = await remote.ssh.script(
                    node, scripts.PREPARE, [worker["root"], worker["owner"]]
                )
                available = dict(
                    line.split("=", 1) for line in prepared.splitlines() if "=" in line
                ).get("DiskAvailable")
                if (
                    available
                    and int(available)
                    < release["archive_path"].stat().st_size * 4 + 100 * 1024**2
                ):
                    raise ManagerError(
                        "disk_full", "远端剩余磁盘不足以安装和保留旧版本"
                    )
                await remote.jobs.phase(job, "uploading", "正在流式上传并校验 RPC 包")
                await remote.ssh.upload(
                    node, release["archive_path"], worker["root"] + "/package.part"
                )
                devices = {d["id"]: d for d in node["snapshot"]["devices"]}
                if not set(worker["device_ids"]) <= devices.keys():
                    raise ManagerError(
                        "device_unavailable", "登记的设备已变化，请核对节点"
                    )
                names = ",".join(
                    "CUDA" + str(devices[d]["backend_index"])
                    for d in worker["device_ids"]
                )
                cuda = (
                    node["snapshot"]["inventory"].get("cuda_version")
                    or "/usr/local/cuda"
                )
                import re

                if not re.fullmatch(r"/usr/local/cuda(?:-[0-9.]+)?", cuda):
                    cuda = "/usr/local/cuda"
                await remote.jobs.phase(
                    job, "installing", "正在校验运行库并配置受管 RPC 服务"
                )
                old = dict(worker)
                switched = False
                try:
                    if old.get("release_id"):
                        await remote.ssh.execute(
                            node,
                            "systemctl --user stop "
                            + shlex.quote(worker["service_name"]),
                            timeout=20,
                        )
                    switched = True
                    installed = await remote.ssh.script(
                        node,
                        scripts.INSTALL,
                        [
                            worker["root"],
                            release["id"],
                            release["sha256"],
                            worker["service_name"],
                            worker["endpoint"]["host"],
                            port,
                            names,
                            worker["owner"],
                            cuda + "/lib64",
                        ],
                        timeout=120,
                    )
                    signatures = dict(
                        line.split("=", 1)
                        for line in installed.splitlines()
                        if "=" in line
                    )
                    if not signatures.get("UnitSHA256") or not signatures.get(
                        "RunnerSHA256"
                    ):
                        raise ManagerError(
                            "deployment_incomplete",
                            "部署未返回可核验的服务与启动脚本摘要",
                        )
                    worker.update(
                        release_id=release["id"],
                        runtime_commit=release["commit"],
                        package_sha256=release["sha256"],
                        expected_binary_sha256=release["files"]["bin/ggml-rpc-server"],
                        expected_unit_sha256=signatures["UnitSHA256"],
                        expected_runner_sha256=signatures["RunnerSHA256"],
                        protocol_version=None,
                        state="stopped",
                        last_error=None,
                    )
                    await remote.store.put("workers", worker)
                    if body.start:
                        await remote.jobs.phase(
                            job, "starting", "正在启动并验证 RPC 协议和设备"
                        )
                        await remote.start_unlocked(worker)
                    else:
                        await remote.refresh_worker(worker)
                    await remote.ssh.execute(
                        node,
                        "systemctl --user "
                        + ("enable " if body.autostart else "disable ")
                        + shlex.quote(worker["service_name"]),
                    )
                    worker["autostart"] = body.autostart
                    await remote.store.put("workers", worker)
                except BaseException:
                    if switched:
                        try:
                            try:
                                await remote.ssh.execute(
                                    node,
                                    "systemctl --user stop "
                                    + shlex.quote(worker["service_name"]),
                                    timeout=20,
                                )
                            except Exception:
                                pass  # A failed first install may have no unit to stop.
                            await remote.ssh.script(
                                node,
                                scripts.ROLLBACK,
                                [
                                    worker["root"],
                                    worker["service_name"],
                                    worker["owner"],
                                ],
                            )
                            worker.clear()
                            worker.update(old)
                            worker["state"] = (
                                "stopped" if old.get("release_id") else "failed"
                            )
                            worker["last_error"] = {
                                "code": "deployment_failed",
                                "message": "新版本未通过验证，已保留旧版本；请核对后手动启动",
                            }
                            await remote.store.put("workers", worker)
                        except Exception:
                            worker["state"] = "unknown"
                            await remote.store.put("workers", worker)
                    raise
                return remote.public_worker(worker)

        await remote.assert_idle([identifier])
        return await remote.jobs.submit(
            worker["node_id"], "deploy", work, secrets=[secret] if secret else []
        )

    async def adopt(self, identifier, body):
        remote = self.remote
        worker = remote.worker(identifier)

        async def work(job):
            async with remote.operations:
                await remote.assert_idle([identifier])
                if worker["management_mode"] != "observed" or not worker.get(
                    "service_name"
                ):
                    raise ManagerError("worker_not_observed", "请先登记外部服务名称")
                node = remote.node(worker["node_id"])
                await remote.collect(node)
                release = await remote.releases.get(body.release_id, node)
                status = await remote.status(worker)
                expected = release["files"]["bin/ggml-rpc-server"]
                if (
                    status.get("ExecutableSHA256") != expected
                    or int(status.get("MainPID", "0")) == 0
                ):
                    raise ManagerError(
                        "service_identity_changed",
                        "已有服务必须运行，且其 RPC 二进制 SHA256 必须与匹配发布包相同",
                    )
                await remote.verify_rpc(worker)
                worker.update(
                    management_mode="managed",
                    controller_id=remote.store.controller_id,
                    imported=True,
                    release_id=release["id"],
                    runtime_commit=release["commit"],
                    expected_binary_sha256=expected,
                    service_signature=status.get("ExecStart"),
                    executable_path=status.get("Executable"),
                    process_identity={
                        "pid": int(status["MainPID"]),
                        "start_time": status.get("ProcessStart"),
                        "boot_id": status.get("BootID"),
                    },
                )
                await remote.store.put("workers", worker)
                return remote.public_worker(worker)

        return await remote.jobs.submit(worker["node_id"], "adopt", work)
