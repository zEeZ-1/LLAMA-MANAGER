#!/usr/bin/env python3
"""ISOLATED QA ONLY: manager SIGKILL, native RPC standby, no model auto-reload."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import httpx
import psutil
from backend.app.runtime.rpc_probe import probe

ROOT = Path(__file__).resolve().parents[1]
QA = Path(os.getenv("LIM_QA_ROOT", "/tmp/lim-v2-qa"))
URL = os.getenv("LIM_QA_URL", "http://127.0.0.1:8012") + "/api/v1"
UNIT = (
    "llama-manager-"
    + hashlib.sha256(str(QA / "manager.db").encode()).hexdigest()[:12]
    + ".service"
)


def wait(fn, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("Lifecycle condition timed out")


def main():
    flow = json.loads((QA / "flow.json").read_text())
    with httpx.Client(base_url=URL, trust_env=False, timeout=30) as client:
        instance = client.get("/instances/" + flow["instances"][0]).json()
        assert instance["status"] == "ready", instance
        rpc_workers = [
            client.get("/nodes/" + nid + "/workers").json()["workers"][0]
            for nid in flow["nodes"]
        ]
        rpc_pids = [worker["process_identity"]["pid"] for worker in rpc_workers]
        main_pid = int(
            subprocess.check_output(
                ["systemctl", "--user", "show", UNIT, "-p", "MainPID", "--value"]
            )
        )
        masters = [
            p
            for p in psutil.Process(main_pid).children(recursive=True)
            if p.name() == "ssh" and "-M" in p.cmdline()
        ]
        for master in masters:
            master.terminate()
        response = client.post(
            "/instances/" + instance["id"] + "/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": False,
                "max_tokens": 12,
            },
        )
        assert response.status_code == 200, response.text
        print("SSH-only disconnection preserves real RPC chat", flush=True)
        model = client.get("/models").json()["models"][0]
        local_response = client.post(
            "/instances",
            json={
                "name": "V2 local crash QA",
                "model_id": model["id"],
                "config": {
                    "context_size": 256,
                    "threads": 2,
                    "batch_size": 64,
                    "ubatch_size": 32,
                },
            },
        )
        local_response.raise_for_status()
        local = local_response.json()
        wait(
            lambda: client.get("/instances/" + local["id"]).json()["status"] == "ready"
        )
        pids = [
            instance["runtime"]["pid"],
            client.get("/instances/" + local["id"]).json()["runtime"]["pid"],
        ]
        assert all(UNIT in Path(f"/proc/{pid}/cgroup").read_text() for pid in pids)
        assert UNIT in Path(f"/proc/{main_pid}/cgroup").read_text()
        os.kill(main_pid, signal.SIGKILL)
        wait(
            lambda: all(
                not psutil.pid_exists(pid)
                or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
                for pid in pids
            ),
            15,
        )
        assert all(psutil.pid_exists(pid) for pid in rpc_pids)
        print(
            "Manager SIGKILL: owned llama-server exited; RPC stayed alive", flush=True
        )

    async def verify_workers():
        return await asyncio.gather(
            *(
                probe(worker["endpoint"]["host"], worker["endpoint"]["port"], 5)
                for worker in rpc_workers
            )
        )

    assert all(p["protocol_version"] == "10.0.0" for p in asyncio.run(verify_workers()))
    log = (QA / "manager-restarted.log").open("wb")
    launcher = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "-m", "backend.app"],
        cwd=ROOT,
        env={**os.environ, "LIM_CONFIG": str(QA / "config.yaml")},
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()
    (QA / "launcher-pid").write_text(str(launcher.pid))

    def healthy():
        try:
            return (
                httpx.get(URL + "/health", trust_env=False, timeout=1).status_code
                == 200
            )
        except httpx.HTTPError:
            return False

    wait(healthy, 40)
    with httpx.Client(base_url=URL, trust_env=False, timeout=30) as client:
        restored = [
            client.get("/instances/" + identifier).json()
            for identifier in [instance["id"], local["id"]]
        ]
        assert all(
            r["status"] == "failed" and "manager_terminated" in r["error_message"]
            for r in restored
        ), restored
        assert not any(
            i["status"] == "ready" for i in client.get("/instances").json()["instances"]
        )
        assert (
            not client.get("/nodes/" + flow["nodes"][0] + "/workers")
            .json()["workers"][0]
            .get("claim")
        )
        restarted_pid = int(
            subprocess.check_output(
                ["systemctl", "--user", "show", UNIT, "-p", "MainPID", "--value"]
            )
        )
        assert restarted_pid != main_pid
    result = {
        "checks": [
            "SSH-only loss preserves RPC inference",
            "owned children share manager cgroup",
            "SIGKILL manager stops all owned llama-server",
            "RPC services accept a fresh session after old session cleanup",
            "restart does not reload models",
            "stale records reconciled and claims released",
        ],
        "killed_manager_pid": main_pid,
        "terminated_inference_pids": pids,
        "rpc_standby_pids": rpc_pids,
        "restarted_manager_pid": restarted_pid,
    }
    (QA / "lifecycle.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
