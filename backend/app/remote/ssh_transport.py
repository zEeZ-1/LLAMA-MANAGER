import asyncio
import base64
import hashlib
import os
from pathlib import Path
import shlex
import signal
import time
import uuid
from contextlib import asynccontextmanager
from ..core.errors import ManagerError


class SshTransport:
    def __init__(self, cfg):
        self.cfg = cfg
        self.root = cfg.ssh.private_directory
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.masters = {}
        self.node_locks = {}
        self.pool_lock = asyncio.Lock()
        self.slots = asyncio.Semaphore(cfg.ssh.max_parallel_commands)
        self.pending = 0
        self.closed = False
        self.task = None
        self.credential_resolver = None

    def known_hosts(self, node):
        return self.root / (node["id"] + ".known_hosts")

    @staticmethod
    def host_pattern(node):
        address, port = node["address"], node["ssh"]["port"]
        return address if port == 22 else f"[{address}]:{port}"

    def repair_legacy_host(self, node):
        """Fix the old port-22 spelling only for the exact already-trusted key."""
        path = self.known_hosts(node)
        if node["ssh"]["port"] != 22 or not path.is_file():
            return False
        content = path.read_text()
        fields = content.split()
        if len(fields) != 3 or fields[0] != f"[{node['address']}]:22":
            return False
        try:
            fingerprint = "SHA256:" + base64.b64encode(
                hashlib.sha256(base64.b64decode(fields[2], validate=True)).digest()
            ).decode().rstrip("=")
        except ValueError:
            return False
        if fingerprint != node.get("identity", {}).get("ssh_host_key_fingerprint"):
            return False
        path.write_text(f"{self.host_pattern(node)} {fields[1]} {fields[2]}\n")
        path.chmod(0o600)
        return True

    async def run(self, argv, stdin=None, timeout=None, env=None, limit=None):
        limit = limit or self.cfg.ssh.max_output_bytes
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE
            if stdin is not None
            else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )

        async def read(stream):
            output = bytearray()
            while part := await stream.read(8192):
                output.extend(part)
                if len(output) > limit:
                    raise ManagerError(
                        "output_limit", "Remote command exceeded its output limit"
                    )
            return bytes(output)

        async def write():
            if stdin is not None:
                try:
                    proc.stdin.write(stdin)
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    proc.stdin.close()

        tasks = [
            asyncio.create_task(read(proc.stdout)),
            asyncio.create_task(read(proc.stderr)),
            asyncio.create_task(write()),
        ]
        try:
            async with asyncio.timeout(timeout or self.cfg.ssh.command_timeout_seconds):
                output, error, _ = await asyncio.gather(*tasks)
                code = await proc.wait()
                return (
                    code,
                    output.decode(errors="replace"),
                    error.decode(errors="replace"),
                )
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            # Drain pipe buffers after killing the process group. Otherwise a paused
            # asyncio pipe transport can keep Process.wait() pending after OS exit.
            async def discard(stream):
                while await stream.read(8192):
                    pass

            await asyncio.gather(discard(proc.stdout), discard(proc.stderr))
            await proc.wait()

    def options(self, node):
        result = [
            "-F",
            "/dev/null",
            "-T",
            "-p",
            str(node["ssh"]["port"]),
            "-l",
            node["ssh"]["username"],
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.known_hosts(node)}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            f"ConnectTimeout={self.cfg.ssh.connect_timeout_seconds}",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "NumberOfPasswordPrompts=1",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
        ]
        if self.credential_resolver:
            key = self.credential_resolver(node["ssh"].get("credential_id"))
            if key:
                result += ["-i", str(key), "-o", "IdentitiesOnly=yes"]
        return result

    @staticmethod
    def ssh_error(error):
        if "REMOTE HOST IDENTIFICATION HAS CHANGED" in error:
            return ManagerError(
                "host_key_changed", "SSH 主机密钥不匹配，请重新核验指纹"
            )
        if "host key is known" in error or "Host key verification failed" in error:
            return ManagerError(
                "host_key_untrusted",
                "SSH 未找到可用的受信主机记录，请重新检测并核验指纹",
            )
        if "Permission denied" in error:
            return ManagerError("auth_failed", "SSH 认证失败，请检查用户名、密码或密钥")
        return ManagerError(
            "ssh_unreachable", ("SSH 连接或命令失败：" + error.strip())[:1200]
        )

    async def scan(self, node):
        code, out, error = await self.run(
            [
                "ssh-keyscan",
                "-T",
                str(self.cfg.ssh.connect_timeout_seconds),
                "-p",
                str(node["ssh"]["port"]),
                "-t",
                "ed25519,ecdsa,rsa",
                node["address"],
            ],
            timeout=self.cfg.ssh.connect_timeout_seconds * 3 + 2,
        )
        keys = []
        for line in out.splitlines():
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 3:
                continue
            try:
                raw = base64.b64decode(parts[2], validate=True)
            except ValueError:
                continue
            fp = "SHA256:" + base64.b64encode(
                hashlib.sha256(raw).digest()
            ).decode().rstrip("=")
            keys.append({"algorithm": parts[1], "fingerprint": fp, "key": parts[2]})
        if not keys:
            raise self.ssh_error(error or "未收到 SSH 主机公钥")
        keys.sort(key=lambda key: key["algorithm"] != "ssh-ed25519")
        return keys

    @asynccontextmanager
    async def password_bridge(self, password):
        filename = self.root / ("ask-" + uuid.uuid4().hex[:12])
        secret = password.encode()

        async def accepted(reader, writer):
            try:
                writer.write(secret)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(accepted, path=str(filename))
        filename.chmod(0o600)
        helper = Path(__file__).with_name("askpass.py")
        helper.chmod(0o755)
        try:
            yield {
                **os.environ,
                "SSH_ASKPASS": str(helper),
                "SSH_ASKPASS_REQUIRE": "force",
                "DISPLAY": "lim:0",
                "LIM_ASKPASS_SOCKET": str(filename),
            }
        finally:
            server.close()
            await server.wait_closed()
            filename.unlink(missing_ok=True)
            secret = b""

    async def _master(self, node, password=None):
        self.repair_legacy_host(node)
        if not self.known_hosts(node).exists():
            raise ManagerError("host_key_untrusted", "请先核对并信任 SSH 主机指纹")
        async with self.pool_lock:
            if len(str(self.root).encode()) > 78:
                raise ManagerError(
                    "ssh_path_too_long",
                    "SSH 私有目录路径过长，请配置较短的 data/ssh 路径",
                )
            previous = self.masters.get(node["id"])
            if previous and previous["proc"].returncode is None:
                previous["used"] = time.monotonic()
                return previous
            if previous:
                await self._close(node["id"])
            if len(self.masters) >= self.cfg.ssh.max_connections:
                idle = sorted(
                    (v for v in self.masters.values() if not v["active"]),
                    key=lambda v: v["used"],
                )
                if not idle:
                    raise ManagerError("ssh_pool_busy", "SSH 连接池忙，请稍后重试", 429)
                await self._close(idle[0]["node_id"])
            path = self.root / ("c-" + node["id"][:16])
            path.unlink(missing_ok=True)
            options = self.options(node)
            options += [
                "-o",
                "BatchMode=no" if password is not None else "BatchMode=yes",
            ]
            if password is not None:
                options += [
                    "-o",
                    "PubkeyAuthentication=no",
                    "-o",
                    "PreferredAuthentications=password,keyboard-interactive",
                ]
            argv = [
                "ssh",
                *options,
                "-M",
                "-N",
                "-S",
                str(path),
                "-o",
                "ControlPersist=no",
                node["address"],
            ]

            async def launch(env=None):
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )
                error = bytearray()

                async def drain():
                    while part := await proc.stderr.read(4096):
                        error.extend(part)
                        if len(error) > self.cfg.ssh.max_output_bytes:
                            del error[: -self.cfg.ssh.max_output_bytes]

                drainer = asyncio.create_task(drain())
                entry = dict(
                    proc=proc,
                    path=path,
                    error=error,
                    drainer=drainer,
                    node_id=node["id"],
                    active=0,
                    used=time.monotonic(),
                )
                self.masters[node["id"]] = entry
                try:
                    async with asyncio.timeout(
                        self.cfg.ssh.connect_timeout_seconds + 5
                    ):
                        while not path.exists():
                            if proc.returncode is not None:
                                await drainer
                                raise self.ssh_error(error.decode(errors="replace"))
                            await asyncio.sleep(0.05)
                except BaseException:
                    await self._close(node["id"])
                    raise
                return entry

            if password is not None:
                async with self.password_bridge(password) as env:
                    return await launch(env)
            return await launch()

    async def execute(self, node, command, stdin=None, timeout=None, password=None):
        if self.pending >= self.cfg.ssh.max_pending_commands:
            raise ManagerError("ssh_queue_full", "SSH 任务队列已满", 429)
        self.pending += 1
        try:
            async with (
                self.slots,
                self.node_locks.setdefault(node["id"], asyncio.Lock()),
            ):
                master = await self._master(node, password)
                master["active"] += 1
                try:
                    code, out, error = await self.run(
                        [
                            "ssh",
                            *self.options(node),
                            "-o",
                            "BatchMode=yes",
                            "-S",
                            str(master["path"]),
                            node["address"],
                            command,
                        ],
                        stdin,
                        timeout,
                    )
                    if code != 0:
                        if code == 255:
                            raise self.ssh_error(error)
                        raise ManagerError(
                            "remote_command_failed",
                            (error or out or f"command exited {code}")[-1600:],
                        )
                    return out
                except TimeoutError as exc:
                    raise ManagerError("ssh_timeout", "远程命令执行超时") from exc
                finally:
                    master["active"] -= 1
                    master["used"] = time.monotonic()
        finally:
            self.pending -= 1

    async def script(self, node, script, args=(), **kwargs):
        command = "sh -s --" + "".join(" " + shlex.quote(str(arg)) for arg in args)
        return await self.execute(node, command, script.encode(), **kwargs)

    async def upload(self, node, source, target):
        # SFTP subsystem over the already authenticated OpenSSH master; no remote shell expansion.
        async with self.slots, self.node_locks.setdefault(node["id"], asyncio.Lock()):
            master = await self._master(node)
            master["active"] += 1
            try:
                options = self.options(node)
                # scp uses -P for port and does not accept ssh's -T/-l flags.
                options = [
                    "-F",
                    "/dev/null",
                    "-P",
                    str(node["ssh"]["port"]),
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ControlPath={master['path']}",
                    "-o",
                    "StrictHostKeyChecking=yes",
                    "-o",
                    f"UserKnownHostsFile={self.known_hosts(node)}",
                    "-o",
                    "GlobalKnownHostsFile=/dev/null",
                ]
                code, out, error = await self.run(
                    [
                        "scp",
                        "-q",
                        *options,
                        str(source),
                        f"{node['ssh']['username']}@{node['address']}:{target}",
                    ],
                    timeout=self.cfg.jobs.transfer_timeout_seconds,
                )
                if code:
                    raise ManagerError(
                        "transfer_failed",
                        (error or out or f"SFTP exited with code {code}")[-1200:],
                    )
            finally:
                master["active"] -= 1
                master["used"] = time.monotonic()

    async def _close(self, node_id):
        entry = self.masters.pop(node_id, None)
        if not entry:
            return
        if entry["proc"].returncode is None:
            entry["proc"].terminate()
            try:
                await asyncio.wait_for(entry["proc"].wait(), 2)
            except TimeoutError:
                entry["proc"].kill()
                await entry["proc"].wait()
        await asyncio.gather(entry["drainer"], return_exceptions=True)
        entry["path"].unlink(missing_ok=True)

    async def close_node(self, node_id):
        async with self.pool_lock:
            await self._close(node_id)

    async def reap_idle(self):
        async with self.pool_lock:
            for node_id, entry in list(self.masters.items()):
                if (
                    not entry["active"]
                    and time.monotonic() - entry["used"]
                    > self.cfg.ssh.control_persist_seconds
                ):
                    await self._close(node_id)

    async def close(self):
        async with self.pool_lock:
            for node_id in list(self.masters):
                await self._close(node_id)
