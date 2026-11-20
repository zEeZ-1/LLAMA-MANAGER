#!/usr/bin/env python3
"""Isolated native CUDA RPC service lifecycle on the local Linux/systemd host.

Creates a UUID test unit/directory, always stops and removes only those resources.
No SSH keys or user model services are modified.
"""

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import httpx

from backend.app.core.config import load_settings
from backend.app.remote import scripts
from backend.app.remote.releases import ReleaseCatalog
from backend.app.runtime.rpc_probe import probe
from backend.app.services.node_manager import LocalNodeProvider


async def main(args):
    cfg = load_settings()
    snapshot = await LocalNodeProvider().get()
    release = await ReleaseCatalog(cfg).get(args.release, {"snapshot": snapshot})
    suffix = uuid.uuid4().hex[:12]
    unit = "llama-manager-qa-rpc-" + suffix + ".service"
    relative = ".local/share/llama-manager/qa-validation-" + suffix
    owner = "qa-validation:" + suffix
    base = Path.home() / relative
    unit_path = Path.home() / ".config/systemd/user" / unit
    result = {
        "release": args.release,
        "unit": unit,
        "hardware_model": snapshot.get("hardware_model"),
        "checks": [],
    }
    inference = None

    def shell(script, values):
        return subprocess.run(
            ["sh", "-s", "--", *map(str, values)],
            input=script,
            text=True,
            capture_output=True,
            timeout=120,
            check=True,
        ).stdout

    def systemctl(*values, check=True):
        return subprocess.run(
            ["systemctl", "--user", *values],
            capture_output=True,
            text=True,
            timeout=20,
            check=check,
        )

    try:
        shell(scripts.PREPARE, [relative, owner])
        shutil.copyfile(release["archive_path"], base / "package.part")
        installed = shell(
            scripts.INSTALL,
            [
                relative,
                release["id"],
                release["sha256"],
                unit,
                "127.0.0.5",
                18662,
                "CUDA0",
                owner,
                "/usr/local/cuda/lib64",
            ],
        )
        assert "UnitSHA256=" in installed and "RunnerSHA256=" in installed
        # This host has no readable user journal; capture the test unit's output.
        service_log = args.output.with_suffix(".rpc.log")
        unit_path.write_text(
            unit_path.read_text()
            + "\n[Service]\nStandardOutput=append:"
            + str(service_log)
            + "\nStandardError=append:"
            + str(service_log)
            + "\n"
        )
        systemctl("daemon-reload")
        systemctl("start", unit)
        deadline = time.monotonic() + 30
        while True:
            try:
                protocol = await probe("127.0.0.5", 18662, 2)
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                await asyncio.sleep(0.2)
        assert (
            protocol["protocol_version"] == cfg.rpc.protocol_version
            and len(protocol["devices"]) == 1
        )
        status = dict(
            line.split("=", 1)
            for line in shell(scripts.SERVICE_STATUS, [unit]).splitlines()
            if "=" in line
        )
        assert (
            status["ActiveState"] == "active"
            and status["ExecutableSHA256"] == release["files"]["bin/ggml-rpc-server"]
        )
        result.update(
            protocol=protocol,
            idle_rss_bytes=int(status["RSSBytes"]),
            checks=[
                "real remote installation shell",
                "systemd user unit started native CUDA0 worker",
                "original RPC HELLO/device/memory handshake",
                "running executable SHA256 matched release",
            ],
        )
        if args.model:
            log_path = args.output.with_suffix(".inference.log")
            with log_path.open("wb") as output:
                inference = subprocess.Popen(
                    [
                        str(cfg.runtime.llama_cpp.server_binary),
                        "-m",
                        str(args.model.resolve()),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "18664",
                        "--rpc",
                        "127.0.0.5:18662",
                        "--device",
                        "RPC0",
                        "--split-mode",
                        "layer",
                        "--tensor-split",
                        "1",
                        "--fit",
                        "off",
                        "--flash-attn",
                        "off",
                        "-ngl",
                        "99",
                        "-c",
                        "256",
                        "-b",
                        "64",
                        "-ub",
                        "32",
                        "-t",
                        "2",
                        "-lv",
                        "4",
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            async with httpx.AsyncClient(
                base_url="http://127.0.0.1:18664", trust_env=False, timeout=30
            ) as client:
                deadline = time.monotonic() + 60
                while True:
                    assert inference.poll() is None, log_path.read_text()[-4000:]
                    try:
                        if (await client.get("/health")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert time.monotonic() < deadline, "CUDA RPC model health timeout"
                    await asyncio.sleep(0.2)
                reply = await client.post(
                    "/v1/chat/completions",
                    json={
                        "messages": [
                            {"role": "user", "content": "Tell a short story."}
                        ],
                        "stream": True,
                        "max_tokens": 24,
                    },
                )
                assert reply.status_code == 200 and "data: [DONE]" in reply.text, (
                    reply.text
                )
            inference.terminate()
            await asyncio.to_thread(inference.wait, 10)
            native_log = log_path.read_text()
            assert "RPC0" in native_log
            result["gpu_inference"] = {
                "model": args.model.name,
                "stream_done": True,
                "log_evidence": [
                    line
                    for line in native_log.splitlines()
                    if "RPC0" in line or "offloaded" in line
                ],
            }
            result["checks"].append(
                "tiny model inference through native RPC0 on real Orin CUDA0; SSE completed; worker accepted a fresh session after model exit"
            )
            await probe("127.0.0.5", 18662, 3)
        systemctl("stop", unit)
        assert "ActiveState=inactive" in shell(scripts.SERVICE_STATUS, [unit])
        previous_runner = (base / "run.sh").read_bytes()
        previous_unit = unit_path.read_bytes()
        # Same release is immutable; reinstall and rollback preserve it.
        shutil.copyfile(release["archive_path"], base / "package.part")
        shell(
            scripts.INSTALL,
            [
                relative,
                release["id"],
                release["sha256"],
                unit,
                "127.0.0.5",
                18662,
                "CUDA0",
                owner,
                "/usr/local/cuda/lib64",
            ],
        )
        shell(scripts.ROLLBACK, [relative, unit, owner])
        assert (
            previous_runner == (base / "run.sh").read_bytes()
            and previous_unit == unit_path.read_bytes()
        )
        result["checks"].append(
            "real unit stopped; immutable reinstall and rollback preserved configuration"
        )
        args.output.write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)
    finally:
        if inference is not None and inference.poll() is None:
            inference.kill()
            inference.wait(timeout=10)
        systemctl("stop", unit, check=False)
        unit_path.unlink(missing_ok=True)
        systemctl("daemon-reload", check=False)
        systemctl("reset-failed", unit, check=False)
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    asyncio.run(main(parser.parse_args()))
