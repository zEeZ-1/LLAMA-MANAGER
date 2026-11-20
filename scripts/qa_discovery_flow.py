#!/usr/bin/env python3
"""Isolated loopback SSH/RPC discovery, recovery and targeted deletion QA."""

import argparse
import json
from pathlib import Path
import sqlite3
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/tmp/lim-rpc-discovery-qa"))
    parser.add_argument("--url", default="http://127.0.0.1:8017")
    parser.add_argument("--ssh-root", type=Path, default=Path("/tmp/lim-disc-ssh"))
    args = parser.parse_args()
    assert (
        args.url.startswith("http://127.0.0.1:")
        and args.root.is_relative_to("/tmp")
        and args.ssh_root.is_relative_to("/tmp")
    )
    endpoints = json.loads((args.root / "ssh-endpoints.json").read_text())
    result = {
        "checks": [],
        "nodes": [],
        "scope": "simulated Linux/SSH service metadata with native OpenSSH, SFTP and CPU RPC over loopback",
    }
    with httpx.Client(
        base_url=args.url + "/api/v1", trust_env=False, timeout=60
    ) as client:

        def call(method, path, body=None):
            response = client.request(method, path, json=body)
            response.raise_for_status()
            return response.json()

        def job(record, success=True):
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                value = call("GET", "/jobs/" + record["id"])
                if value["state"] in {"completed", "failed", "interrupted"}:
                    assert value["state"] == ("completed" if success else "failed"), (
                        value
                    )
                    return value
                time.sleep(0.1)
            raise AssertionError("job deadline")

        def connect(node, password="lim-isolated-qa-password", success=True):
            return job(
                call(
                    "POST", "/nodes/" + node["id"] + "/connect", {"password": password}
                ),
                success,
            )

        nodes = []
        for i, e in enumerate(endpoints[:2]):
            node = call(
                "POST",
                "/nodes",
                {
                    "name": f"Discovery QA {i + 1}",
                    "address": e["address"],
                    "port": e["port"],
                    "username": e["username"],
                },
            )
            challenge = call("POST", "/nodes/" + node["id"] + "/host-key/scan")
            assert e["fingerprint"] in [k["fingerprint"] for k in challenge["keys"]]
            call(
                "POST",
                "/nodes/" + node["id"] + "/host-key/trust",
                {
                    "challenge_id": challenge["challenge_id"],
                    "fingerprint": e["fingerprint"],
                },
            )
            connect(node)
            node = call("GET", "/nodes/" + node["id"])
            assert (
                node["auth_state"]["automatic"] and node["rpc_discovery"]["count"] == 1
            ), node
            assert (
                len(node["rpc_workers"]) == 1
                and node["rpc_workers"][0]["state"] == "ready"
            )
            assert node["rpc_workers"][0]["origin"] == "existing"
            nodes.append(node)
            result["nodes"].append(node["id"])
        result["checks"].append(
            "password login automatically discovers usable existing RPC without manual worker registration or adoption"
        )
        print(result["checks"][-1], flush=True)

        model = call("GET", "/models")["models"][0]
        config = {
            "context_size": 512,
            "batch_size": 64,
            "ubatch_size": 32,
            "threads": 2,
            "n_gpu_layers": 99,
            "placement": {
                "mode": "rpc",
                "devices": [
                    {
                        "device_id": n["rpc_workers"][0]["device_ids"][0],
                        "worker_id": n["rpc_workers"][0]["id"],
                        "weight": 1,
                    }
                    for n in nodes
                ],
            },
        }
        instance = call(
            "POST",
            "/instances",
            {
                "name": "Discovery native RPC QA",
                "model_id": model["id"],
                "config": config,
            },
        )
        for _ in range(240):
            instance = call("GET", "/instances/" + instance["id"])
            if instance["status"] in {"ready", "failed"}:
                break
            time.sleep(0.25)
        assert instance["status"] == "ready", instance

        def chat():
            r = client.post(
                "/instances/" + instance["id"] + "/chat/completions",
                json={
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                    "max_tokens": 12,
                },
            )
            assert r.status_code == 200 and "data: [DONE]" in r.text, r.text

        chat()
        failure = connect(nodes[0], password="QA-WRONG-PASSWORD", success=False)
        assert failure["error"]["code"] == "auth_failed"
        chat()
        connect(nodes[0])
        chat()
        result["checks"].append(
            "password is freshly checked even with a live key master; SSH auth failure and recovery do not interrupt native inference"
        )
        print(result["checks"][-1], flush=True)
        old = list(args.ssh_root.glob("c-" + nodes[0]["id"][:12] + "-*"))
        for path in old:
            path.unlink(missing_ok=True)
        job(call("POST", "/nodes/" + nodes[0]["id"] + "/refresh"))
        chat()
        assert list(args.ssh_root.glob("c-" + nodes[0]["id"][:12] + "-*"))
        result["checks"].append(
            "missing/stale SSH control socket is reaped and reconnected without relying on password persistence"
        )

        # Lose only this fixture's local private key, then recover using a password.
        with sqlite3.connect(
            "file:" + str(args.root / "manager.db") + "?mode=ro", uri=True
        ) as db:
            credential_id = call("GET", "/nodes/" + nodes[0]["id"])["ssh"][
                "credential_id"
            ]
            credentials = [
                json.loads(row[0])
                for row in db.execute("select record from credentials")
            ]
        key = Path(next(c["path"] for c in credentials if c["id"] == credential_id))
        assert key.is_relative_to(args.ssh_root)
        saved = key.with_suffix(".qa-backup")
        key.rename(saved)
        try:
            connect(nodes[0])
            chat()
            assert call("GET", "/nodes/" + nodes[0]["id"])["auth_state"]["automatic"]
        finally:
            saved.rename(key)
        connect(nodes[1])
        result["checks"].append(
            "missing stored private key does not block password authentication; new generated key is verified and reused"
        )

        fresh = call("GET", "/nodes/" + nodes[0]["id"])
        new = call(
            "POST",
            "/nodes/" + fresh["id"] + "/workers",
            {"device_ids": fresh["rpc_workers"][0]["device_ids"]},
        )
        assert new["endpoint"]["port"] != fresh["rpc_workers"][0]["endpoint"]["port"]
        release = call("GET", "/releases")["releases"][-1]["id"]
        job(
            call("POST", "/workers/" + new["id"] + "/deploy", {"release_id": release}),
            success=True,
        )
        workers = call("GET", "/nodes/" + fresh["id"] + "/workers")["workers"]
        new = next(w for w in workers if w["id"] == new["id"])
        collision = {
            "name": "must not claim same GPU twice",
            "model_id": model["id"],
            "config": {
                **config,
                "placement": {
                    "mode": "rpc",
                    "devices": [
                        {
                            "device_id": new["device_ids"][0],
                            "worker_id": new["id"],
                            "weight": 1,
                        }
                    ],
                },
            },
        }
        assert client.post("/instances", json=collision).status_code == 409
        old_worker = next(w for w in workers if w["id"] != new["id"])
        assert (
            client.request(
                "DELETE", "/workers/" + old_worker["id"], json={}
            ).status_code
            == 409
        )
        job(
            call(
                "DELETE",
                "/workers/" + new["id"],
                {"process_identity": new["process_identity"]},
            )
        )
        chat()
        result["checks"].append(
            "new RPC uses free port; same GPU cannot be double claimed; deleting idle new RPC preserves active existing RPC inference"
        )
        print(result["checks"][-1], flush=True)
        call("DELETE", "/instances/" + instance["id"])
        job(call("POST", "/nodes/" + nodes[0]["id"] + "/refresh"))
        current = call("GET", "/nodes/" + nodes[0]["id"])["rpc_workers"][0]
        job(
            call(
                "DELETE",
                "/workers/" + current["id"],
                {"process_identity": current["process_identity"]},
            )
        )
        assert not call("GET", "/nodes/" + nodes[0]["id"])["rpc_workers"]
        assert (
            call("GET", "/nodes/" + nodes[1]["id"])["rpc_workers"][0]["state"]
            == "ready"
        )
        result["checks"].append(
            "deleting discovered external user service stops only its identified RPC; other node and external executable remain intact"
        )
        result["deleted_existing_worker"] = current["id"]
        result["instance"] = instance["id"]
        (args.root / "discovery-flow.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
