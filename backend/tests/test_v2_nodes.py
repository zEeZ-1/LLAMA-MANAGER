import asyncio
import gc
import json
from unittest.mock import AsyncMock

import pytest
import psutil

from backend.app.core.errors import ManagerError
from backend.app.nodes.telemetry import public_snapshot, snapshot
from backend.app.repositories.instances import InstanceRepository
from backend.app.repositories.manager import ManagerStore
from backend.app.services.job_manager import JobManager
from backend.app.services.resource_manager import ResourceManager


def test_repositories_close_short_lived_connections_without_gc(cfg):
    async def run():
        repo = InstanceRepository(cfg.storage.database)
        await repo.initialize()
        store = ManagerStore(cfg.storage.database)
        await store.initialize()
        enabled = gc.isenabled()
        gc.disable()
        try:
            for _ in range(40):
                await store.put("nodes", {"id": "qa", "name": "QA"})
                await store.all("nodes")
                await store.claims()
                await repo.save({"id": "i"})
                await repo.all()
            open_database_files = [
                f.path
                for f in psutil.Process().open_files()
                if f.path.startswith(str(cfg.storage.database))
            ]
            assert not open_database_files, open_database_files
        finally:
            if enabled:
                gc.enable()

    asyncio.run(run())


def test_dynamic_inventory_keeps_platform_without_retaining_machine_id():
    raw = dict(
        hostname="qa",
        architecture="aarch64",
        cpu_count="6",
        cpu_name="ARM",
        hardware_model="Jetson Orin",
        l4t="R36",
        glibc="glibc 2.39",
        machine_id="private-raw-identifier",
        memory_total="8000",
        memory_available="4000",
    )
    first = snapshot(raw, "n")
    second = snapshot(
        {
            "inventory_mode": "dynamic",
            "memory_total": "8000",
            "memory_available": "3000",
        },
        "n",
        first,
    )
    assert (
        second["architecture"] == "aarch64" and second["cpu_info"]["logical_count"] == 6
    )
    assert (
        second["inventory"]["machine_id_hash"] == first["inventory"]["machine_id_hash"]
    )
    assert "private-raw-identifier" not in json.dumps(second)
    assert second["devices"][0]["memory_kind"] == "shared"
    public = public_snapshot(second)
    public["devices"][0]["compute_state"] = "ready"
    assert public_snapshot(second)["devices"][0]["compute_state"] == "unverified"
    assert second["devices"][0]["compute_state"] == "unverified"


def test_host_key_challenge_is_target_bound_expires_and_requires_explicit_replacement(
    client,
):
    remote = client.app.state.remote
    key = {
        "algorithm": "ssh-ed25519",
        "fingerprint": "SHA256:qa",
        "key": "test-public-key",
    }
    remote.ssh.scan = AsyncMock(return_value=[key])
    node = client.post(
        "/api/v1/nodes",
        json={"name": "QA", "address": "192.168.50.201", "username": "qa"},
    ).json()
    prefix = "/api/v1/nodes/" + node["id"]
    challenge = client.post(prefix + "/host-key/scan").json()
    client.patch(prefix, json={"port": 2222})
    body = {
        "challenge_id": challenge["challenge_id"],
        "fingerprint": key["fingerprint"],
    }
    assert (
        client.post(prefix + "/host-key/trust", json=body).json()["detail"]["code"]
        == "challenge_expired"
    )
    challenge = client.post(prefix + "/host-key/scan").json()
    body["challenge_id"] = challenge["challenge_id"]
    assert client.post(prefix + "/host-key/trust", json=body).status_code == 200
    key["fingerprint"] = "SHA256:changed"
    challenge = client.post(prefix + "/host-key/scan").json()
    body.update(challenge_id=challenge["challenge_id"], fingerprint=key["fingerprint"])
    response = client.post(prefix + "/host-key/trust", json=body)
    assert (
        response.status_code == 409
        and response.json()["detail"]["code"] == "host_key_changed"
    )
    challenge = client.post(prefix + "/host-key/scan").json()
    remote.challenges[challenge["challenge_id"]]["expires"] = 0
    body["challenge_id"] = challenge["challenge_id"]
    assert (
        client.post(prefix + "/host-key/trust", json=body).json()["detail"]["code"]
        == "challenge_expired"
    )


def test_worker_edit_and_mutation_never_take_an_occupied_worker(client):
    remote = client.app.state.remote
    node = client.post(
        "/api/v1/nodes",
        json={"name": "QA", "address": "192.168.50.202", "username": "qa"},
    ).json()
    node_id = node["id"]
    remote.nodes[node_id]["snapshot"] = snapshot(
        {"hardware_model": "Jetson Orin"}, node_id
    )
    device = remote.nodes[node_id]["snapshot"]["devices"][0]["id"]
    worker = client.post(
        f"/api/v1/nodes/{node_id}/workers", json={"device_ids": [device]}
    ).json()
    wid = worker["id"]
    client.portal.call(client.app.state.store.reserve, [wid], "instance")
    assert client.post(f"/api/v1/workers/{wid}/stop", json={}).status_code == 409
    assert (
        client.patch("/api/v1/nodes/" + node_id, json={"name": "new"}).status_code
        == 409
    )
    assert client.delete("/api/v1/nodes/" + node_id).status_code == 409
    client.portal.call(client.app.state.store.claim_state, "instance", "released")
    remote.workers[wid]["state"] = "ready"
    assert (
        client.patch(
            "/api/v1/nodes/" + node_id, json={"address": "192.168.50.203"}
        ).status_code
        == 409
    )
    remote.workers[wid]["state"] = "stopped"
    assert (
        client.patch(
            "/api/v1/nodes/" + node_id, json={"address": "192.168.50.203"}
        ).status_code
        == 200
    )
    assert remote.workers[wid]["endpoint"]["host"] == "192.168.50.203"


def test_job_duplicate_submission_secret_redaction_restart_and_early_cancellation(cfg):
    async def run():
        await InstanceRepository(cfg.storage.database).initialize()
        store = ManagerStore(cfg.storage.database)
        await store.initialize()
        jobs = JobManager(store, cfg)
        await jobs.initialize()
        event = asyncio.Event()

        async def work(identifier):
            await event.wait()
            raise ManagerError("qa_failure", "contains QA_SECRET_VALUE")

        first, second = await asyncio.gather(
            jobs.submit("node", "connect", work, secrets=["QA_SECRET_VALUE"]),
            jobs.submit("node", "connect", work, secrets=["QA_SECRET_VALUE"]),
        )
        assert first["id"] == second["id"] and len(jobs.tasks) == 1
        event.set()
        await asyncio.gather(*list(jobs.tasks.values()))
        assert first["state"] == "failed" and "[redacted]" in first["error"]["message"]
        assert "QA_SECRET_VALUE" not in json.dumps(await store.all("jobs"))

        async def pending(identifier):
            await asyncio.Event().wait()

        queued = await jobs.submit("other", "start", pending)
        await jobs.cancel(queued["id"])
        assert queued["state"] == "interrupted" and not jobs.tasks
        interrupted = {**queued, "id": "interrupted-on-restart", "state": "running"}
        await store.put("jobs", interrupted)
        restarted = JobManager(store, cfg)
        await restarted.initialize()
        assert restarted.records[interrupted["id"]]["state"] == "interrupted"
        assert cfg.storage.database.with_suffix(".pre-v2.bak").is_file()

    asyncio.run(run())


def test_release_quarantines_unknown_rpc_and_reconciles_after_proof(cfg):
    async def run():
        await InstanceRepository(cfg.storage.database).initialize()
        store = ManagerStore(cfg.storage.database)
        await store.initialize()
        remote = AsyncMock()
        remote.store = store
        remote.assert_managed = AsyncMock()
        remote.worker = lambda wid: {"id": wid, "state": "ready"}
        remote.verify_rpc.side_effect = ManagerError("rpc_unreachable", "unknown")
        resources = ResourceManager(remote, None)
        await store.reserve(["w"], "i")
        await resources._release_locked("i")
        assert (await store.claims())[0]["state"] == "quarantined"
        with pytest.raises(ManagerError):
            await store.reserve(["w"], "other")
        remote.verify_rpc.side_effect = None
        await resources._release_locked("i")
        assert not await store.claims()

    asyncio.run(run())
