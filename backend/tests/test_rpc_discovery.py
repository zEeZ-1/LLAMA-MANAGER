import base64
from unittest.mock import AsyncMock

import pytest

from backend.app.core.errors import ManagerError
from backend.app.nodes.telemetry import snapshot
from backend.app.remote.discovery import parse_discovery, connection_matches
from backend.app.schemas.instance import InstanceConfig
from .conftest import wait_for


def encoded(value):
    return base64.b64encode(value.encode()).decode()


def wire(processes):
    lines = ["boot\tqa-boot", "uid\t2002"]
    for p in processes:
        lines.append(
            f'listen\tLISTEN 0 1 192.168.50.220:{p["port"]} 0.0.0.0:* users:(("ggml-rpc-server",pid={p["pid"]},fd=3))'
        )
        if p.get("busy"):
            lines.append(
                f'connection\t0 0 192.168.50.220:{p["port"]} 192.168.50.1:12345 users:(("ggml-rpc-server",pid={p["pid"]},fd=4))'
            )
    for p in processes:
        group = p.get(
            "group",
            "0::/user.slice/user-2002.slice/user@2002.service/app.slice/" + p["unit"],
        )
        args = (
            "ggml-rpc-server\0--host\0192.168.50.220\0--port\0"
            + str(p["port"])
            + "\0--device\0CUDA0\0"
        )
        lines.append(
            "\t".join(
                [
                    "process",
                    str(p["pid"]),
                    str(p["start"]),
                    str(p.get("uid", 2002)),
                    "100000",
                    encoded("/opt/ggml-rpc-server"),
                    p.get("digest", "a" * 64),
                    encoded(group),
                    encoded(args),
                ]
            )
        )
    return "\n".join(lines) + "\n"


@pytest.fixture
def discovery_fixture(client, monkeypatch):
    remote = client.app.state.remote
    node = client.post(
        "/api/v1/nodes",
        json={"name": "Discovery QA", "address": "192.168.50.220", "username": "qa"},
    ).json()
    node = remote.node(node["id"])
    node["snapshot"] = snapshot(
        {
            "hardware_model": "Jetson Orin",
            "architecture": "aarch64",
            "machine_id": "qa-machine",
            "linger": "yes",
        },
        node["id"],
    )
    node["connection_state"] = "online"
    node["identity"]["machine_id_hash"] = node["snapshot"]["inventory"][
        "machine_id_hash"
    ]
    state = {
        "processes": [
            {
                "pid": 101,
                "start": 1001,
                "port": 50052,
                "unit": "llama-rpc-worker.service",
            }
        ],
        "removed": [],
        "key_failure": False,
    }
    remote.releases.records = lambda: [
        {
            "id": "qa-release",
            "files": {"bin/ggml-rpc-server": "a" * 64},
            "commit": remote.cfg.rpc.expected_commit,
            "protocol_version": remote.cfg.rpc.protocol_version,
        }
    ]

    async def script(target, text, args=(), **kwargs):
        if "# LIM_OP discover_rpc" in text:
            return wire(state["processes"])
        if "# LIM_OP service_status" in text:
            p = next((p for p in state["processes"] if p["unit"] == args[0]), None)
            if not p:
                return "LoadState=not-found\nMainPID=0\nActiveState=inactive\n"
            return "\n".join(
                f"{k}={v}"
                for k, v in {
                    "LoadState": "loaded",
                    "ActiveState": "active",
                    "MainPID": p["pid"],
                    "ProcessStart": str(p["start"]),
                    "BootID": "qa-boot",
                    "Executable": "/opt/ggml-rpc-server",
                    "ExecutableSHA256": p.get("digest", "a" * 64),
                    "RSSBytes": 100000,
                    "ExecStart": "QA " + p["unit"],
                    "FragmentPath": "/home/qa/.config/systemd/user/" + p["unit"],
                }.items()
            )
        if "# LIM_OP remove_external_rpc" in text:
            state["removed"].append(list(args))
            state["processes"] = [p for p in state["processes"] if p["pid"] != args[0]]
            return ""
        if "# LIM_OP uninstall_rpc" in text:
            state["removed"].append(list(args))
            state["processes"] = [p for p in state["processes"] if p["unit"] != args[1]]
            return ""
        if "# LIM_OP install_key" in text:
            if state["key_failure"]:
                raise ManagerError(
                    "remote_command_failed", "cannot write authorized_keys"
                )
            return ""
        if "Bounded, read-only inventory" in text:
            return "hostname\tqa\narchitecture\taarch64\nhardware_model\tJetson Orin\nmachine_id\tqa-machine\nlinger\tyes\n"
        return ""

    remote.ssh.script = AsyncMock(side_effect=script)
    remote.ssh.verify_key = AsyncMock()
    probe = AsyncMock(
        return_value={
            "protocol_version": "10.0.0",
            "devices": [{"index": 0, "memory_free": 100, "memory_total": 200}],
        }
    )
    monkeypatch.setattr("backend.app.services.remote_manager.probe", probe)
    return remote, node, state, probe


def scan(client, fixture):
    remote, node, state, probe = fixture
    client.portal.call(remote.discovery.scan, node)
    return remote.node_workers(node["id"])


def job(client, record):
    value = wait_for(
        lambda: (
            r
            if (r := client.get("/api/v1/jobs/" + record["id"]).json())["state"]
            in {"completed", "failed", "interrupted"}
            else None
        )
    )
    return value


def test_discovery_reuses_placeholder_and_matches_version_device_and_service(
    client, discovery_fixture
):
    remote, node, state, probe = discovery_fixture
    device = node["snapshot"]["devices"][0]["id"]
    placeholder = client.post(
        f"/api/v1/nodes/{node['id']}/workers", json={"device_ids": [device]}
    ).json()
    workers = scan(client, discovery_fixture)
    assert len(workers) == 1 and workers[0]["id"] == placeholder["id"]
    worker = workers[0]
    assert (
        worker["origin"] == "existing"
        and worker["state"] == "ready"
        and worker["usable"]
    )
    assert (
        worker["service_name"] == "llama-rpc-worker.service"
        and worker["service_signature"] == "QA llama-rpc-worker.service"
    )
    assert worker["device_ids"] == [device] and worker["management_mode"] == "managed"
    assert node["rpc_discovery"]["suggested_port"] == 50053
    assert probe.await_count == 1
    public = client.get("/api/v1/nodes/" + node["id"]).json()
    assert public["rpc_workers"][0]["process_identity"]["pid"] == 101


def test_busy_rpc_is_not_probed_and_unknown_version_stays_visible(
    client, discovery_fixture
):
    remote, node, state, probe = discovery_fixture
    state["processes"][0]["busy"] = True
    worker = scan(client, discovery_fixture)[0]
    assert worker["state"] == "busy" and not worker["usable"]
    probe.assert_not_awaited()
    state["processes"][0]["busy"] = False
    state["processes"][0]["digest"] = "b" * 64
    worker = scan(client, discovery_fixture)[0]
    assert (
        worker["state"] == "unverified"
        and worker["last_error"]["code"] == "version_unverified"
    )
    assert worker["can_delete"] and worker["service_signature"]
    probe.assert_not_awaited()


def test_claimed_rpc_keeps_identity_and_is_not_probed(client, discovery_fixture):
    remote, node, state, probe = discovery_fixture
    worker = scan(client, discovery_fixture)[0]
    client.portal.call(remote.store.reserve, [worker["id"]], "i")
    state["processes"][0]["busy"] = True
    scan(client, discovery_fixture)
    assert (
        worker["state"] == "ready"
        and worker["protocol_version"] == "10.0.0"
        and probe.await_count == 1
    )
    state["processes"][0]["pid"] = 102
    state["processes"][0]["start"] = 1002
    scan(client, discovery_fixture)
    assert (
        worker["state"] == "failed"
        and worker["protocol_version"] is None
        and probe.await_count == 1
    )


def test_multiple_rpc_on_same_gpu_cannot_be_claimed_by_different_instances(
    client, discovery_fixture
):
    remote, node, state, probe = discovery_fixture
    state["processes"].append(
        {"pid": 102, "start": 1002, "port": 50053, "unit": "second-rpc.service"}
    )
    first, second = scan(client, discovery_fixture)
    assert node["rpc_discovery"]["suggested_port"] == 50054

    def config(worker):
        return InstanceConfig(
            n_gpu_layers=1,
            placement={
                "mode": "rpc",
                "devices": [
                    {"device_id": worker["device_ids"][0], "worker_id": worker["id"]}
                ],
            },
        )

    client.portal.call(client.app.state.resources.reserve, "first", config(first))
    with pytest.raises(ManagerError) as exc:
        client.portal.call(client.app.state.resources.reserve, "second", config(second))
    assert exc.value.code == "worker_busy"


def test_password_login_detects_rpc_before_optional_key_setup_and_warns_if_it_fails(
    client, discovery_fixture
):
    remote, node, state, probe = discovery_fixture
    state["key_failure"] = True
    result = job(
        client,
        client.post(
            f"/api/v1/nodes/{node['id']}/connect",
            json={"password": "synthetic-password"},
        ).json(),
    )
    assert result["state"] == "completed"
    assert (
        node["connection_state"] == "online"
        and node["auth_state"]["automatic"] is False
    )
    assert remote.node_workers(node["id"])[0]["state"] == "ready"
    assert "synthetic-password" not in str(result)
    assert node["auth_state"]["warning"]


def test_delete_rpc_checks_claims_identity_and_active_connections(
    client, discovery_fixture
):
    remote, node, state, probe = discovery_fixture
    worker = scan(client, discovery_fixture)[0]
    path = "/api/v1/workers/" + worker["id"]
    client.portal.call(remote.store.reserve, [worker["id"]], "i")
    assert client.request("DELETE", path, json={}).status_code == 409
    client.portal.call(remote.store.claim_state, "i", "released")
    bad = {**worker["process_identity"], "pid": 999}
    result = job(
        client, client.request("DELETE", path, json={"process_identity": bad}).json()
    )
    assert result["error"]["code"] == "rpc_identity_changed" and not state["removed"]
    state["processes"][0]["busy"] = True
    result = job(client, client.request("DELETE", path, json={}).json())
    assert result["error"]["code"] == "rpc_in_use" and not state["removed"]
    result = job(
        client,
        client.request(
            "DELETE",
            path,
            json={"process_identity": worker["process_identity"], "force": True},
        ).json(),
    )
    assert result["state"] == "completed" and state["removed"], result
    assert not remote.node_workers(node["id"])
    assert client.get("/api/v1/nodes/" + node["id"]).json()["rpc_workers"] == []


def test_plain_process_is_not_mistaken_for_user_manager_service():
    record = {
        "pid": 101,
        "start": 1001,
        "port": 50052,
        "unit": "unused",
        "group": "0::/user.slice/user-2002.slice/user@2002.service/app.slice/terminal.scope",
    }
    process = parse_discovery(wire([record]), "192.168.50.220")["processes"][0]
    assert process["service_scope"] == "process" and process["service_name"] is None


def test_connections_on_other_addresses_do_not_make_rpc_busy():
    line = '0 0 192.168.50.221:50052 192.168.50.1:1234 users:(("ggml-rpc-server",pid=202,fd=4))'
    assert not connection_matches(line, "101", 50052, "192.168.50.220")
    assert connection_matches(line, "202", 50052, "192.168.50.221")


def test_explicit_machine_replacement_preserves_history_and_requires_idle_resources(
    client, discovery_fixture
):
    remote, node, state, probe = discovery_fixture
    worker = scan(client, discovery_fixture)[0]
    node["identity"]["ssh_host_key_fingerprint"] = "SHA256:old"
    node["ssh"]["credential_id"] = "old-credential"
    remote.ssh.scan = AsyncMock(
        return_value=[
            {"algorithm": "ssh-ed25519", "key": "cXVh", "fingerprint": "SHA256:new"}
        ]
    )
    prefix = "/api/v1/nodes/" + node["id"]

    def body():
        challenge = client.post(prefix + "/host-key/scan").json()
        return {
            "challenge_id": challenge["challenge_id"],
            "fingerprint": "SHA256:new",
            "replace": True,
            "replace_machine": True,
        }

    client.portal.call(remote.store.reserve, [worker["id"]], "i")
    assert (
        client.post(prefix + "/host-key/trust", json=body()).json()["detail"]["code"]
        == "worker_busy"
    )
    assert node["identity"]["ssh_host_key_fingerprint"] == "SHA256:old"
    client.portal.call(remote.store.claim_state, "i", "released")
    assert client.post(prefix + "/host-key/trust", json=body()).status_code == 200
    assert not remote.node_workers(node["id"]) and worker["deleted_at"]
    assert (
        node["identity"]["machine_id_hash"] is None
        and node["ssh"]["credential_id"] is None
    )
    assert (
        node["identity"]["ssh_host_key_fingerprint"] == "SHA256:new"
        and node["snapshot"] == {}
    )


def test_null_confirmation_does_not_delete_a_newly_started_rpc(
    client, discovery_fixture
):
    remote, node, state, probe = discovery_fixture
    worker = scan(client, discovery_fixture)[0]
    worker["process_identity"] = None
    result = job(
        client,
        client.request(
            "DELETE", "/api/v1/workers/" + worker["id"], json={"process_identity": None}
        ).json(),
    )
    assert result["error"]["code"] == "rpc_identity_changed" and not state["removed"]


def test_password_is_redacted_from_persisted_node_errors(client, discovery_fixture):
    remote, node, state, probe = discovery_fixture
    secret = "QA-PRIVATE-PASSWORD"
    remote.ssh.script = AsyncMock(
        side_effect=ManagerError("ssh_unreachable", "connection failed " + secret)
    )
    result = job(
        client,
        client.post(
            "/api/v1/nodes/" + node["id"] + "/connect", json={"password": secret}
        ).json(),
    )
    assert secret not in str(result) and secret not in str(node["last_error"])
