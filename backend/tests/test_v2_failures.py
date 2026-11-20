import asyncio
import hashlib
import io
import os
import subprocess
import tarfile
import threading
import time
from unittest.mock import AsyncMock

import pytest
import yaml

from backend.app.core.errors import ManagerError
from backend.app.remote import scripts
from backend.app.remote.releases import ReleaseCatalog
from backend.app.remote.ssh_transport import SshTransport


def test_slow_rpc_preparation_returns_202_and_can_be_cancelled(client):
    entered, cancelled = threading.Event(), threading.Event()

    async def prepare(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    resources = AsyncMock()
    resources.prepare.side_effect = prepare
    manager = client.app.state.manager
    original = manager.resources
    manager.resources = resources
    try:
        model = client.get("/api/v1/models").json()["models"][0]
        start = time.monotonic()
        response = client.post(
            "/api/v1/instances",
            json={
                "name": "cancel slow RPC setup",
                "model_id": model["id"],
                "config": {
                    "n_gpu_layers": 1,
                    "placement": {
                        "mode": "rpc",
                        "devices": [
                            {
                                "device_id": "node:cuda:0",
                                "worker_id": "worker",
                                "weight": 1,
                            }
                        ],
                    },
                },
            },
        )
        assert response.status_code == 202, response.text
        assert time.monotonic() - start < 1
        assert entered.wait(1)
        record = response.json()
        assert record["status"] == "starting" and record["runtime"]["pid"] is None
        result = client.delete("/api/v1/instances/" + record["id"])
        assert result.json()["status"] == "stopped"
        assert cancelled.is_set()
        resources.release.assert_awaited()
        assert not manager.start_tasks
    finally:
        manager.resources = original


def test_remote_install_and_rollback_execute_real_shell(tmp_path):
    home = tmp_path / "home"
    commands = tmp_path / "commands"
    commands.mkdir()
    fake_systemctl = commands / "systemctl"
    fake_systemctl.write_text("#!/bin/sh\nexit 0\n")
    fake_systemctl.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(commands) + ":" + os.environ["PATH"],
    }
    rel, owner, unit = ".local/share/test-worker", "qa:worker", "qa-rpc.service"
    base = home / rel

    def run(script, *args, check=True):
        return subprocess.run(
            ["sh", "-s", "--", *args],
            input=script,
            text=True,
            env=env,
            capture_output=True,
            check=check,
        )

    run(scripts.PREPARE, rel, owner)
    package = base / "package.part"

    def install(identifier):
        with tarfile.open(package, "w:gz") as tar:
            tar.add("/bin/true", arcname="bin/ggml-rpc-server")
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        result = run(
            scripts.INSTALL,
            rel,
            identifier,
            digest,
            unit,
            "127.0.0.1",
            "50052",
            "CPU",
            owner,
            "/usr/local/cuda/lib64",
        )
        assert "UnitSHA256=" in result.stdout and "RunnerSHA256=" in result.stdout
        return base / "releases" / (identifier + "-" + digest)

    version1 = install("first")
    runner1 = (base / "run.sh").read_bytes()
    unit_path = home / ".config/systemd/user" / unit
    unit1 = unit_path.read_bytes()
    assert version1.is_dir()
    version2 = install("second")
    assert version1.is_dir() and version2.is_dir()
    run(scripts.ROLLBACK, rel, unit, owner)
    assert (base / "run.sh").read_bytes() == runner1
    assert unit_path.read_bytes() == unit1
    # A checksum failure before switching preserves the CURRENT version, not a stale backup.
    package.write_bytes(b"broken")
    result = run(
        scripts.INSTALL,
        rel,
        "bad",
        "0" * 64,
        unit,
        "127.0.0.1",
        "50052",
        "CPU",
        owner,
        "/usr/local/cuda/lib64",
        check=False,
    )
    assert result.returncode != 0
    run(scripts.ROLLBACK, rel, unit, owner)
    assert (base / "run.sh").read_bytes() == runner1
    assert not list((base / "releases").glob(".stage-*"))
    signature = run(scripts.CHECK_OWNER, rel, owner, unit).stdout
    assert hashlib.sha256(runner1).hexdigest() in signature
    assert (
        run(scripts.CHECK_OWNER, rel, "wrong-owner", unit, check=False).returncode != 0
    )


@pytest.mark.parametrize(
    "kind",
    [
        "archive_digest",
        "binary_digest",
        "traversal",
        "symlink",
        "duplicate",
        "noncanonical",
    ],
)
def test_release_rejects_corrupt_or_unsafe_packages(cfg, kind):
    cfg.rpc.packages_directory.mkdir(parents=True)
    archive = cfg.rpc.packages_directory / "qa.tar.gz"
    payload = b"qa binary"
    with tarfile.open(archive, "w:gz") as tar:

        def add(name, content):
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))

        add("bin/ggml-rpc-server", payload)
        if kind == "traversal":
            add("../outside", b"escape")
        if kind == "symlink":
            entry = tarfile.TarInfo("link")
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/outside"
            tar.addfile(entry)
        if kind == "duplicate":
            add("bin/ggml-rpc-server", b"replaced")
        if kind == "noncanonical":
            add("bin/./ggml-rpc-server", b"replaced")
    record = dict(
        id="qa",
        architecture="aarch64",
        platform="jetson",
        glibc_min="2.35",
        commit=cfg.rpc.expected_commit,
        protocol_version=cfg.rpc.protocol_version,
        archive=archive.name,
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        files={"bin/ggml-rpc-server": hashlib.sha256(payload).hexdigest()},
    )
    if kind == "archive_digest":
        record["sha256"] = "0" * 64
    if kind == "binary_digest":
        record["files"]["bin/ggml-rpc-server"] = "0" * 64
    cfg.rpc.deployment_manifest.parent.mkdir(parents=True, exist_ok=True)
    cfg.rpc.deployment_manifest.write_text(yaml.safe_dump({"releases": [record]}))
    node = {
        "snapshot": {
            "architecture": "aarch64",
            "inventory": {"glibc": "glibc 2.39", "l4t": "R36"},
            "devices": [{}],
        }
    }
    with pytest.raises(ManagerError) as error:
        asyncio.run(ReleaseCatalog(cfg).get("qa", node))
    assert error.value.code in {"checksum_mismatch", "package_invalid"}


def test_ssh_command_output_limit_and_timeout_reap_process(cfg):
    async def run():
        transport = SshTransport(cfg)
        with pytest.raises(ManagerError) as error:
            await transport.run(["sh", "-c", "yes x"], limit=1024)
        assert error.value.code == "output_limit"
        with pytest.raises(TimeoutError):
            await transport.run(["sleep", "30"], timeout=0.05)
        await transport.close()

    asyncio.run(run())
