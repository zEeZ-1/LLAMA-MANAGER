import hashlib
import os
from pathlib import Path
import shutil
import subprocess

from backend.app.remote.rpc_scripts import REMOVE_EXTERNAL, UNINSTALL


def test_process_removal_requires_exact_start_boot_and_executable(tmp_path):
    binary = tmp_path / "ggml-rpc-server"
    shutil.copyfile("/bin/sleep", binary)
    binary.chmod(0o755)
    proc = subprocess.Popen([str(binary), "30"], start_new_session=True)
    try:
        start = Path(f"/proc/{proc.pid}/stat").read_text().split(") ", 1)[1].split()[19]
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()

        def run(stamp, checksum):
            return subprocess.run(
                ["sh", "-s", "--", str(proc.pid), stamp, boot, checksum, "", "", "no"],
                input=REMOVE_EXTERNAL,
                text=True,
                capture_output=True,
                timeout=10,
            )

        wrong = run(str(int(start) + 1), digest)
        assert (
            wrong.returncode != 0
            and "rpc_identity_changed" in wrong.stderr
            and proc.poll() is None
        )
        wrong = run(start, "0" * 64)
        assert (
            wrong.returncode != 0
            and "rpc_identity_changed" in wrong.stderr
            and proc.poll() is None
        )
        correct = run(start, digest)
        assert correct.returncode == 0, correct.stderr
        assert proc.wait(timeout=5) < 0
        assert binary.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_uninstall_removes_only_owned_service_directory(tmp_path):
    home = tmp_path / "home"
    commands = tmp_path / "commands"
    commands.mkdir()
    systemctl = commands / "systemctl"
    systemctl.write_text("#!/bin/sh\nexit 0\n")
    systemctl.chmod(0o755)
    relative = ".local/share/llama-manager/aaaa/bbbb"
    base = home / relative
    base.mkdir(parents=True)
    (base / "owner").write_text("controller:worker")
    (base / "cache").mkdir()
    (base / "cache/weights").write_text("owned cache")
    neighbor = base.parent / "unrelated"
    neighbor.mkdir()
    (neighbor / "keep").write_text("keep")
    unit = home / ".config/systemd/user/qa-rpc.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("owned unit")
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(commands) + ":" + os.environ["PATH"],
    }
    wrong = subprocess.run(
        ["sh", "-s", "--", relative, unit.name, "wrong"],
        input=UNINSTALL,
        text=True,
        env=env,
        capture_output=True,
    )
    assert wrong.returncode != 0 and base.exists() and unit.exists()
    correct = subprocess.run(
        ["sh", "-s", "--", relative, unit.name, "controller:worker"],
        input=UNINSTALL,
        text=True,
        env=env,
        capture_output=True,
    )
    assert correct.returncode == 0, correct.stderr
    assert (
        not base.exists()
        and not unit.exists()
        and (neighbor / "keep").read_text() == "keep"
    )
