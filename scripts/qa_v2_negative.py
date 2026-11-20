#!/usr/bin/env python3
"""Negative tests against the UUID workers of the isolated loopback V2 fixture."""

import json
import os
from pathlib import Path
import signal
import subprocess
import time

import httpx
import psutil


def main():
    root = Path("/tmp/lim-v2-final")
    ssh_root = Path("/tmp/lim2-final-ssh")
    checks = []
    with httpx.Client(
        base_url="http://127.0.0.1:8013/api/v1", trust_env=False, timeout=30
    ) as client:

        def call(method, path, body=None):
            response = client.request(method, path, json=body)
            response.raise_for_status()
            return response.json()

        def job(record, failed=False):
            for _ in range(200):
                value = call("GET", "/jobs/" + record["id"])
                if value["state"] in ["completed", "failed", "interrupted"]:
                    assert value["state"] == ("failed" if failed else "completed"), (
                        value
                    )
                    return value
                time.sleep(0.1)
            raise AssertionError("job timeout")

        node = next(
            n
            for n in call("GET", "/nodes")["nodes"]
            if n.get("address") == "127.0.0.11"
        )
        prefix = "/nodes/" + node["id"]
        failure = job(
            call("POST", prefix + "/connect", {"password": "QA-INTENTIONALLY-WRONG"}),
            failed=True,
        )
        assert failure["error"][
            "code"
        ] == "auth_failed" and "QA-INTENTIONALLY-WRONG" not in json.dumps(failure)
        job(call("POST", prefix + "/connect", {}))
        checks.append(
            "wrong password rejected with auth_failed; subsequent key recovery works"
        )
        known = ssh_root / (node["id"] + ".known_hosts")
        saved = known.read_text()
        other = (
            next(p for p in ssh_root.glob("*.known_hosts") if p != known)
            .read_text()
            .split()
        )
        try:
            subprocess.run(
                [
                    "ssh",
                    "-F",
                    "/dev/null",
                    "-O",
                    "exit",
                    "-S",
                    str(ssh_root / ("c-" + node["id"][:16])),
                    node["address"],
                ],
                capture_output=True,
                check=False,
            )
            time.sleep(0.3)
            known.write_text(saved.split()[0] + " " + other[1] + " " + other[2] + "\n")
            failure = job(call("POST", prefix + "/connect", {}), failed=True)
            assert failure["error"]["code"] == "host_key_changed", failure
        finally:
            known.write_text(saved)
        job(call("POST", prefix + "/connect", {}))
        checks.append(
            "real OpenSSH rejects changed host key and reconnects only after fixture trust restoration"
        )
        instance = call("GET", "/instances/" + (root / "soak-instance").read_text())
        assert instance["status"] == "ready"
        flow = json.loads((root / "flow.json").read_text())
        worker = call("GET", "/nodes/" + flow["nodes"][0] + "/workers")["workers"][0]
        pid = worker["process_identity"]["pid"]
        command = psutil.Process(pid).cmdline()
        assert "ggml-rpc-server" in command[0] and "127.0.0.2" in command
        os.kill(pid, signal.SIGKILL)
        deadline = time.monotonic() + 20
        while True:
            after = call("GET", "/instances/" + instance["id"])
            if after["status"] == "failed":
                break
            assert time.monotonic() < deadline, after
            time.sleep(0.2)
        inference_pid = instance["runtime"]["pid"]
        assert (
            not psutil.pid_exists(inference_pid)
            or psutil.Process(inference_pid).status() == psutil.STATUS_ZOMBIE
        )
        for _ in range(100):
            workers = [
                call("GET", "/nodes/" + nid + "/workers")["workers"][0]
                for nid in flow["nodes"]
            ]
            if not any(w.get("claim") for w in workers):
                break
            time.sleep(0.1)
        assert not any(w.get("claim") for w in workers), workers
        job(call("POST", "/workers/" + worker["id"] + "/start", {}))
        assert not any(
            i["status"] == "ready" and i["id"] == instance["id"]
            for i in call("GET", "/instances")["instances"]
        )
        checks.append(
            "native RPC crash fails and stops its owned llama-server; claims released after proof; worker restart does not reload model"
        )
        result = {
            "checks": checks,
            "failed_instance": instance["id"],
            "terminated_inference_pid": inference_pid,
        }
        (root / "negative.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
