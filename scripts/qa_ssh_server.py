#!/usr/bin/env python3
"""ISOLATED QA ONLY. Simulated remote OS/SSH + real native RPC over loopback.
Requires asyncssh in a QA environment; never installed or used on production nodes.
"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import time
import asyncssh


class Machine:
    def __init__(self, address, home, binary):
        self.address, self.home, self.binary = address, home, binary
        home.mkdir(parents=True, exist_ok=True)
        self.keys = set()
        self.units = {}
        self.installed = set()
        self.starts = 0
        self.binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        self.server_key = asyncssh.generate_private_key("ssh-ed25519")
        self.password = os.environ["LIM_QA_PASSWORD"]
        self.password_logins = 0
        self.key_logins = 0

    def inventory(self):
        total, available = 8000000000, 5000000000
        return (
            "\n".join(
                f"{k}\t{v}"
                for k, v in dict(
                    hostname="qa-" + self.address.rsplit(".", 1)[1],
                    architecture="aarch64",
                    os="Ubuntu 24.04 QA",
                    kernel="QA",
                    cpu_name="ARM Cortex-A78AE",
                    cpu_count=6,
                    cpu_total=int(time.monotonic() * 100),
                    cpu_idle=int(time.monotonic() * 50),
                    memory_total=total,
                    memory_available=available,
                    swap_used=0,
                    machine_id=self.address,
                    hardware_model="NVIDIA Jetson Orin QA",
                    l4t="R36 QA simulation",
                    glibc="glibc 2.39",
                    cuda_version="/usr/local/cuda-13.2",
                    linger="yes",
                ).items()
            )
            + "\ngpu\t0, Orin (nvgpu), [N/A], [N/A], 0\n"
        )

    async def start(self, unit):
        info = self.units[unit]
        if info.get("proc") and info["proc"].returncode is None:
            return
        output = (self.home / (unit + ".log")).open("ab")
        proc = await asyncio.create_subprocess_exec(
            str(self.binary),
            "--host",
            self.address,
            "--port",
            str(info["port"]),
            "--device",
            "CPU",
            "--cache",
            stdout=output,
            stderr=output,
            env={**os.environ, "LLAMA_CACHE": str(self.home / "cache")},
        )
        output.close()
        info["proc"] = proc
        self.starts += 1
        for _ in range(100):
            if proc.returncode is not None:
                raise RuntimeError(
                    "Native RPC exited; see " + str(self.home / (unit + ".log"))
                )
            try:
                reader, writer = await asyncio.open_connection(
                    self.address, info["port"]
                )
                writer.close()
                await writer.wait_closed()
                return
            except OSError:
                await asyncio.sleep(0.05)
        raise RuntimeError("Native RPC did not listen")

    async def stop(self, unit):
        info = self.units.get(unit, {})
        proc = info.get("proc")
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3)
            except TimeoutError:
                proc.kill()
                await proc.wait()

    async def handle(self, process):
        command = process.command or ""
        content = await process.stdin.read()
        try:
            out = ""
            args = shlex.split(command)
            if command.startswith("sh -s --"):
                values = args[3:]
                if "# LIM_OP install_key" in content:
                    self.keys.add(
                        " ".join(
                            asyncssh.import_public_key(values[0])
                            .export_public_key()
                            .decode()
                            .split()[:2]
                        )
                    )
                    path = self.home / ".ssh/authorized_keys"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(values[0] + "\n")
                elif "# LIM_OP service_status" in content:
                    unit = values[0]
                    info = self.units.get(unit)
                    if not info:
                        out = "LoadState=not-found\nActiveState=inactive\nMainPID=0\n"
                    else:
                        proc = info.get("proc")
                        alive = proc is not None and proc.returncode is None
                        data = {
                            "LoadState": "loaded",
                            "ActiveState": "active" if alive else "inactive",
                            "SubState": "running" if alive else "dead",
                            "MainPID": proc.pid if alive else 0,
                            "ExecMainStatus": 0,
                            "ExecStart": "QA native RPC " + unit,
                            "FragmentPath": "QA/" + unit,
                        }
                        if alive:
                            data.update(
                                BootID="qa-boot",
                                ProcessStart=Path(f"/proc/{proc.pid}/stat")
                                .read_text()
                                .split(") ", 1)[1]
                                .split()[19],
                                Executable=str(self.binary),
                                ExecutableSHA256=self.binary_sha,
                                RSSBytes=next(
                                    (
                                        int(line.split()[1]) * 1024
                                        for line in Path(f"/proc/{proc.pid}/status")
                                        .read_text()
                                        .splitlines()
                                        if line.startswith("VmRSS:")
                                    ),
                                    0,
                                ),
                            )
                        out = "".join(f"{k}={v}\n" for k, v in data.items())
                elif "# LIM_OP prepare" in content:
                    path = self.home / values[0]
                    path.mkdir(parents=True, exist_ok=True)
                    (path / "owner").write_text(values[1])
                    out = "DiskAvailable=10000000000\n"
                elif "# LIM_OP install" in content:
                    root, release, digest, unit, bind, port, device, owner, cuda = (
                        values
                    )
                    archive = self.home / root / "package.part"
                    if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                        raise RuntimeError("QA received wrong archive checksum")
                    self.units[unit] = {"port": int(port), "owner": owner, "root": root}
                    self.installed.add(unit)
                    archive.unlink()
                    out = "UnitSHA256=" + "a" * 64 + "\nRunnerSHA256=" + "b" * 64 + "\n"
                elif "# LIM_OP owner" in content:
                    if (self.home / values[0] / "owner").read_text() != values[1]:
                        raise RuntimeError("QA owner mismatch")
                    out = "UnitSHA256=" + "a" * 64 + "\nRunnerSHA256=" + "b" * 64 + "\n"
                elif "# LIM_OP rollback" in content:
                    pass
                elif "# LIM_OP clean_cache" in content:
                    pass
                elif "Bounded, read-only inventory" in content:
                    out = self.inventory()
                else:
                    raise RuntimeError("QA does not execute unknown remote scripts")
            elif args[:3] == ["systemctl", "--user", "start"]:
                await self.start(args[3])
            elif args[:3] == ["systemctl", "--user", "stop"]:
                await self.stop(args[3])
            elif args[:3] in (
                ["systemctl", "--user", "enable"],
                ["systemctl", "--user", "disable"],
            ):
                pass
            elif args and args[0] == "ss":
                out = ""
            elif args and args[0] == "journalctl":
                unit = args[args.index("-u") + 1]
                path = self.home / (unit + ".log")
                out = (
                    path.read_text(errors="replace")[-32000:]
                    if path.exists()
                    else "No RPC log yet\n"
                )
            else:
                raise RuntimeError("QA does not execute unknown commands: " + command)
            process.stdout.write(out)
            process.exit(0)
        except Exception as exc:
            process.stderr.write(str(exc))
            process.exit(1)


class QASFTPServer(asyncssh.SFTPServer):
    def exit(self):
        self.channel.exit(0)


class SSHServer(asyncssh.SSHServer):
    def __init__(self, machine):
        self.machine = machine

    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def public_key_auth_supported(self):
        return True

    def validate_password(self, username, password):
        valid = password == self.machine.password
        if valid:
            self.machine.password_logins += 1
        return valid

    def validate_public_key(self, username, key):
        valid = (
            " ".join(key.export_public_key().decode().split()[:2]) in self.machine.keys
        )
        if valid:
            self.machine.key_logins += 1
        return valid


async def main(args):
    (args.root / "server-pid").write_text(str(os.getpid()))
    machines = []
    listeners = []
    records = []
    for suffix in range(args.start, args.start + args.count):
        machine = Machine(
            f"127.0.0.{suffix}", args.root / f"node-{suffix}", args.binary.resolve()
        )
        machines.append(machine)
        listener = await asyncssh.create_server(
            lambda m=machine: SSHServer(m),
            machine.address,
            0,
            server_host_keys=[machine.server_key],
            process_factory=machine.handle,
            sftp_factory=lambda chan, m=machine: QASFTPServer(chan, chroot=str(m.home)),
        )
        listeners.append(listener)
        records.append(
            {
                "address": machine.address,
                "port": listener.get_port(),
                "username": "worker",
                "fingerprint": machine.server_key.get_fingerprint(),
            }
        )
    args.output.write_text(json.dumps(records))
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, event.set)
    try:
        await event.wait()
    finally:
        for listener in listeners:
            listener.close()
            await listener.wait_closed()
        for machine in machines:
            for unit in machine.units:
                await machine.stop(unit)
        (args.root / "summary.json").write_text(
            json.dumps(
                [
                    {
                        "address": m.address,
                        "password_logins": m.password_logins,
                        "key_logins": m.key_logins,
                        "rpc_starts": m.starts,
                    }
                    for m in machines
                ]
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--start", type=int, default=2)
    parser.add_argument("--count", type=int, default=3)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    asyncio.run(main(args))
