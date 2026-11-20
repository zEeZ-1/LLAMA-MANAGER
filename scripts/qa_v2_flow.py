#!/usr/bin/env python3
"""ISOLATED QA ONLY: exercise SSH password bootstrap + native two-worker RPC.
Run with the loopback test service and qa_ssh_server.py; not a production-node script.
"""

import argparse
import json
import time
from pathlib import Path
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/tmp/lim-v2-qa"))
    parser.add_argument("--url", default="http://127.0.0.1:8012")
    parser.add_argument("--rpc-port-base", type=int, default=18660)
    args = parser.parse_args()
    assert args.url.startswith("http://127.0.0.1:"), (
        "Use a dedicated loopback QA service"
    )
    records = json.loads((args.root / "ssh-endpoints.json").read_text())
    checks = []
    nodes = []
    workers = []
    instances = []

    def report(message):
        print(message, flush=True)

    with httpx.Client(
        base_url=args.url + "/api/v1", trust_env=False, timeout=120
    ) as client:

        def request(method, path, body=None):
            response = client.request(method, path, json=body)
            if response.is_error:
                raise AssertionError(
                    f"{method} {path}: {response.status_code} {response.text}"
                )
            return response.json()

        def job(record, timeout=120):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                value = request("GET", "/jobs/" + record["id"])
                if value["state"] == "completed":
                    return value
                if value["state"] in {"failed", "interrupted"}:
                    raise AssertionError(value)
                time.sleep(0.1)
            raise AssertionError("Job timed out: " + str(value))

        assert request("GET", "/health")["managed_lifecycle"]
        releases = request("GET", "/releases")["releases"]
        assert releases
        release = releases[0]["id"]
        for index, record in enumerate(records[:2]):
            existing = next(
                (
                    n
                    for n in request("GET", "/nodes")["nodes"]
                    if n.get("address") == record["address"]
                ),
                None,
            )
            node = existing or request(
                "POST",
                "/nodes",
                {
                    "name": f"QA RPC {index + 1}",
                    "address": record["address"],
                    "port": record["port"],
                    "username": record["username"],
                },
            )
            if node.get("ssh", {}).get("port") != record["port"]:
                node = request(
                    "PATCH", "/nodes/" + node["id"], {"port": record["port"]}
                )
            nodes.append(node["id"])
            scanned = request("POST", f"/nodes/{node['id']}/host-key/scan")
            assert record["fingerprint"] in [k["fingerprint"] for k in scanned["keys"]]
            request(
                "POST",
                f"/nodes/{node['id']}/host-key/trust",
                {
                    "challenge_id": scanned["challenge_id"],
                    "fingerprint": record["fingerprint"],
                    "replace": True,
                },
            )
            job(
                request(
                    "POST",
                    f"/nodes/{node['id']}/connect",
                    {"password": "lim-isolated-qa-password"},
                )
            )
            connected = request("GET", "/nodes/" + node["id"])
            assert (
                connected["connection_state"] == "online"
                and connected["ssh"]["credential_id"]
            )
            assert (
                connected["devices"][0]["memory_kind"] == "shared"
                and connected["devices"][0]["memory_total"] is None
            )
            job(request("POST", f"/nodes/{node['id']}/connect", {}))
            report(
                "password bootstrap and subsequent key connection passed: "
                + record["address"]
            )
            current = request("GET", f"/nodes/{node['id']}/workers")["workers"]
            worker = (
                current[0]
                if current
                else request(
                    "POST",
                    f"/nodes/{node['id']}/workers",
                    {
                        "port": args.rpc_port_base + index,
                        "device_ids": [connected["devices"][0]["id"]],
                    },
                )
            )
            workers.append(worker["id"])
            job(
                request(
                    "POST",
                    "/workers/" + worker["id"] + "/deploy",
                    {"release_id": release, "start": True},
                ),
                timeout=180,
            )
            report("release uploaded and native RPC ready: " + record["address"])
        checks.append(
            "two password logins, public-key bootstrap, host fingerprints, shared-memory telemetry, release transfer and native RPC readiness"
        )
        models = request("GET", "/models")["models"]
        assert models
        payload = {
            "name": "V2 distributed QA",
            "model_id": models[0]["id"],
            "config": {
                "context_size": 512,
                "batch_size": 64,
                "ubatch_size": 32,
                "threads": 2,
                "n_gpu_layers": 99,
                "placement": {
                    "mode": "rpc",
                    "devices": [
                        {
                            "device_id": request("GET", "/nodes/" + nid)["devices"][0][
                                "id"
                            ],
                            "worker_id": wid,
                            "weight": 1,
                        }
                        for nid, wid in zip(nodes, workers)
                    ],
                },
            },
        }
        instance = request("POST", "/instances", payload)
        instances.append(instance["id"])
        for _ in range(120):
            instance = request("GET", "/instances/" + instance["id"])
            if instance["status"] in {"ready", "failed"}:
                break
            time.sleep(0.25)
        assert instance["status"] == "ready", instance
        assert instance["runtime"]["placement"]["device_names"] == ["RPC0", "RPC1"]
        conflict = client.post("/instances", json=payload)
        assert conflict.status_code == 409, conflict.text
        assert (
            client.post("/workers/" + workers[0] + "/stop", json={}).status_code == 409
        )
        assert client.post("/nodes/" + nodes[0], json={}).status_code in (404, 405)
        checks.append(
            "native two-worker instance, explicit device mapping, exclusive claims and busy stop protection"
        )
        report("two-worker instance is ready; exclusive claims verified")
        started = time.monotonic()
        events = []
        with client.stream(
            "POST",
            "/instances/" + instance["id"] + "/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Tell a short story."}],
                "stream": True,
                "max_tokens": 24,
            },
        ) as response:
            assert response.status_code == 200, response.read()
            for line in response.iter_lines():
                if line.startswith("data:"):
                    if not events:
                        first = time.monotonic() - started
                    events.append(line)
        assert events[-1] == "data: [DONE]", events[-3:]
        checks.append(
            "real native RPC distributed streaming chat with completion marker"
        )
        report("SSE passed; first event seconds " + str(round(first, 3)))
        result = {
            "checks": checks,
            "nodes": nodes,
            "workers": workers,
            "instances": instances,
            "first_sse_seconds": first,
            "event_count": len(events),
            "native_rpc": True,
            "hardware_environment": "simulated SSH/inventory and native CPU RPC on loopback; not physical remote GPUs",
        }
        (args.root / "flow.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
