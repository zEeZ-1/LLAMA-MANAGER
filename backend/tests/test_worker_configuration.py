from unittest.mock import AsyncMock

from backend.app.nodes.telemetry import snapshot
from .conftest import wait_for


def placeholder(client):
    node = client.post(
        "/api/v1/nodes",
        json={"name": "QA", "address": "192.168.50.210", "username": "qa"},
    ).json()
    remote = client.app.state.remote
    remote.nodes[node["id"]]["snapshot"] = snapshot(
        {"hardware_model": "Jetson Orin", "linger": "yes"}, node["id"]
    )
    device = remote.nodes[node["id"]]["snapshot"]["devices"][0]["id"]
    worker = client.post(
        f"/api/v1/nodes/{node['id']}/workers", json={"device_ids": [device]}
    ).json()
    return node, worker, device


def test_uninstalled_worker_can_change_port_or_choose_existing_service(client):
    node, worker, device = placeholder(client)
    path = "/api/v1/workers/" + worker["id"]
    changed = client.patch(path, json={"device_ids": [device], "port": 50053}).json()
    assert changed["id"] == worker["id"] and changed["endpoint"]["port"] == 50053
    assert changed["state"] == "uninstalled"
    observed = client.patch(
        path,
        json={
            "device_ids": [device],
            "mode": "observed",
            "service_name": "existing-rpc.service",
        },
    ).json()
    assert (
        observed["management_mode"] == "observed"
        and observed["service_name"] == "existing-rpc.service"
    )
    managed = client.patch(
        path, json={"device_ids": [device], "mode": "managed"}
    ).json()
    assert (
        managed["service_name"].startswith("llama-manager-rpc-")
        and managed["state"] == "uninstalled"
    )


def test_edit_refuses_claims_installed_workers_and_missing_devices(client):
    node, worker, device = placeholder(client)
    path = "/api/v1/workers/" + worker["id"]
    assert client.patch(path, json={"device_ids": ["missing"]}).status_code == 409
    client.portal.call(client.app.state.store.reserve, [worker["id"]], "i")
    assert (
        client.patch(path, json={"device_ids": [device], "port": 50053}).json()[
            "detail"
        ]["code"]
        == "worker_busy"
    )
    client.portal.call(client.app.state.store.claim_state, "i", "released")
    client.app.state.remote.workers[worker["id"]]["release_id"] = "installed"
    assert (
        client.patch(path, json={"device_ids": [device], "port": 50053}).json()[
            "detail"
        ]["code"]
        == "worker_config_locked"
    )


def test_deployment_port_conflict_is_actionable_and_does_not_stop_existing_service(
    client,
):
    node, worker, device = placeholder(client)
    remote = client.app.state.remote
    remote.collect = AsyncMock()
    remote.releases.get = AsyncMock(return_value={})
    remote.status = AsyncMock(return_value={"LoadState": "not-found"})
    remote.ssh.execute = AsyncMock(return_value="LISTEN existing process")
    remote.ssh.upload = AsyncMock()
    response = client.post(
        f"/api/v1/workers/{worker['id']}/deploy", json={"release_id": "qa"}
    )
    assert response.status_code == 202
    job = wait_for(
        lambda: (
            j
            if (j := client.get("/api/v1/jobs/" + response.json()["id"]).json())[
                "state"
            ]
            == "failed"
            else None
        )
    )
    assert job["error"]["code"] == "port_conflict"
    assert "192.168.50.210:50052" in job["error"]["message"]
    assert all(
        call.args[1].startswith("ss ") for call in remote.ssh.execute.await_args_list
    )
    remote.ssh.upload.assert_not_awaited()
