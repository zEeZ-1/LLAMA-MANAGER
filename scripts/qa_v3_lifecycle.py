#!/usr/bin/env python3
"""SIGKILL only an isolated V3 unit while native vision + image worker are alive."""

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import httpx
import psutil
import yaml
from PIL import Image

ROOT = Path("/tmp/lim-v3-life")
REPO = Path(__file__).resolve().parents[1]
UNIT = (
    "llama-manager-"
    + hashlib.sha256(str(ROOT / "manager.db").encode()).hexdigest()[:12]
    + ".service"
)
URL = "http://127.0.0.1:8019/api/v1"


def launch():
    env = os.environ.copy()
    env["LIM_CONFIG"] = str(ROOT / "config.yaml")
    env["LD_LIBRARY_PATH"] = "/usr/local/cuda-13.2/lib64:" + env.get(
        "LD_LIBRARY_PATH", ""
    )
    with (ROOT / "manager.log").open("ab") as log:
        return subprocess.Popen(
            [str(REPO / ".venv/bin/python"), "-m", "backend.app"],
            cwd=REPO,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


async def main():
    ROOT.mkdir(exist_ok=True)
    cfg = yaml.safe_load(Path("/tmp/lim-v3-qa/config.yaml").read_text())
    cfg["server"]["port"] = 8019
    cfg["storage"] = {
        k: str(ROOT / v)
        for k, v in [("database", "manager.db"), ("logs", "logs"), ("media", "media")]
    }
    cfg["ssh"]["private_directory"] = str(ROOT / "ssh")
    cfg["instances"]["port_range"] = {"start": 19140, "end": 19150}
    (ROOT / "config.yaml").write_text(yaml.safe_dump(cfg))
    launcher = launch()
    result = {
        "scope": "isolated V3 manager with native CPU vision model; primary manager and real RPCs unaffected"
    }
    async with httpx.AsyncClient(base_url=URL, trust_env=False, timeout=120) as client:

        async def call(method, path, body=None):
            r = await client.request(method, path, json=body)
            r.raise_for_status()
            return r.json()

        for _ in range(100):
            try:
                models = (await call("GET", "/models"))["models"]
                if models:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
        for previous in (await call("GET", "/instances"))["instances"]:
            assert previous["name"] == "V3 crash QA"
            await call("DELETE", "/instances/" + previous["id"])
        model = next(m for m in models if m["name"] == "smolvlm")
        instance = await call(
            "POST",
            "/instances",
            {
                "name": "V3 crash QA",
                "model_id": model["id"],
                "config": {
                    "modality": "image_text",
                    "context_size": 512,
                    "batch_size": 512,
                    "ubatch_size": 512,
                    "threads": 2,
                },
            },
        )
        for _ in range(600):
            instance = await call("GET", "/instances/" + instance["id"])
            if instance["status"] == "failed":
                raise AssertionError(instance["error_message"])
            if instance["status"] == "ready":
                break
            await asyncio.sleep(0.1)
        assert instance["status"] == "ready"
        manager = int(
            subprocess.check_output(
                ["systemctl", "--user", "show", UNIT, "-p", "MainPID", "--value"]
            )
        )
        assert psutil.Process(manager).environ().get("LIM_CONFIG") == str(
            ROOT / "config.yaml"
        )
        image = Image.new("RGB", (4000, 4000), (15, 88, 170))
        data = io.BytesIO()
        image.save(data, format="PNG")
        image.close()
        uploading = asyncio.create_task(
            client.post("/media/images", content=data.getvalue())
        )
        helper = None
        for _ in range(2000):
            for child in psutil.Process(manager).children():
                try:
                    if any(arg.endswith("/media_worker.py") for arg in child.cmdline()):
                        helper = child.pid
                        break
                except psutil.NoSuchProcess:
                    pass
            if helper:
                break
            await asyncio.sleep(0.002)
        assert helper, "No image worker observed"
        native = instance["runtime"]["pid"]
        result.update(manager_pid=manager, native_pid=native, image_worker_pid=helper)
        os.kill(manager, signal.SIGKILL)
        await asyncio.gather(uploading, return_exceptions=True)
        started = time.monotonic()
        for _ in range(150):
            if not psutil.pid_exists(native) and not psutil.pid_exists(helper):
                break
            await asyncio.sleep(0.1)
        assert not psutil.pid_exists(native) and not psutil.pid_exists(helper)
        result["cleanup_seconds"] = time.monotonic() - started
        launcher.wait(timeout=15)
        restored = launch()
        try:
            for _ in range(100):
                try:
                    old = await call("GET", "/instances/" + instance["id"])
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
            assert (
                old["status"] == "failed"
                and "manager_terminated" in old["error_message"]
            )
            assert (await call("GET", "/instances"))["instances"] == [old]
            assert not list((ROOT / "media/staging").iterdir())
            result["checks"] = [
                "SIGKILL removes both native vision process and transient image worker",
                "restart records interrupted instance without model reload",
                "orphan image staging removed on restart",
            ]
        finally:
            subprocess.run(["systemctl", "--user", "stop", UNIT], check=True)
            restored.wait(timeout=15)
    (ROOT / "lifecycle.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
