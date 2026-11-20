import psutil
from fastapi.testclient import TestClient
from backend.app.main import create_app
from .conftest import create, wait_for


def ready(client, instance):
    return wait_for(
        lambda: (
            r
            if (r := client.get("/api/v1/instances/" + instance["id"]).json())["status"]
            == "ready"
            else None
        )
    )


def test_two_independent_instances_chat_and_idempotent_destroy(client):
    first, second = create(client), create(client)
    first, second = ready(client, first), ready(client, second)
    assert first["runtime"]["pid"] != second["runtime"]["pid"]
    assert first["runtime"]["internal_port"] != second["runtime"]["internal_port"]
    assert first["id"] != str(first["runtime"]["pid"])
    for instance in (first, second):
        endpoint = f"/api/v1/instances/{instance['id']}/chat/completions"
        response = client.post(
            endpoint,
            json={"messages": [{"role": "user", "content": "Hello"}], "stream": False},
        )
        assert (
            response.status_code == 200
            and response.json()["choices"][0]["message"]["content"] == "Hello"
        )
        response = client.post(
            endpoint, json={"messages": [{"role": "user", "content": "Hello"}]}
        )
        assert "Hello" in response.text and "[DONE]" in response.text
    url = "/api/v1/instances/" + first["id"]
    assert len(client.get("/api/v1/instances").json()["instances"]) == 2
    assert client.delete(url).json()["status"] == "stopped"
    assert client.delete(url).json()["status"] == "stopped"
    assert not psutil.pid_exists(first["runtime"]["pid"])
    assert (
        client.post(
            url + "/chat/completions",
            json={"messages": [{"role": "user", "content": "x"}]},
        ).status_code
        == 409
    )
    assert client.get("/api/v1/instances/" + second["id"]).json()["status"] == "ready"
    remaining = client.get("/api/v1/instances").json()["instances"]
    assert [r["id"] for r in remaining] == [second["id"]]
    node = client.get("/api/v1/nodes/local").json()
    assert node["instance_count"] == node["ready_count"] == 1
    history = client.get("/api/v1/instances?include_stopped=true").json()["instances"]
    assert {r["id"] for r in history} == {first["id"], second["id"]}
    assert client.get(url).json()["status"] == "stopped"
    assert client.get(url + "/logs").status_code == 200


def test_validation_and_no_arbitrary_arguments(client):
    assert (
        client.post(
            "/api/v1/instances", json={"name": "x", "model_id": "missing"}
        ).status_code
        == 409
    )
    model = client.get("/api/v1/models").json()["models"][0]
    base = {"name": "x", "model_id": model["id"]}
    for config in (
        {"extra_options": {"--host": "0.0.0.0"}},
        {"ubatch_size": 9999},
        {"context_size": 128, "parallel": 32},
        {"cache_type_k": "invalid"},
    ):
        assert (
            client.post(
                "/api/v1/instances", json={**base, "config": config}
            ).status_code
            == 422
        )
    assert (
        client.post(
            "/api/v1/instances", json={**base, "model_path": "/etc/passwd"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/instances", json={**base, "node_id": "unknown"}
        ).status_code
        == 404
    )


def test_startup_timeout_immediate_exit_and_abnormal_exit(client):
    for context, reason in ((128, "timed out"), (129, "exited")):
        record = create(client, context)
        failed = wait_for(
            lambda: (
                r
                if (r := client.get("/api/v1/instances/" + record["id"]).json())[
                    "status"
                ]
                == "failed"
                else None
            )
        )
        assert reason in failed["error_message"]
        assert record["id"] in {
            r["id"] for r in client.get("/api/v1/instances").json()["instances"]
        }
        assert (
            client.get("/api/v1/instances/" + record["id"] + "/logs").status_code == 200
        )
    record = ready(client, create(client))
    psutil.Process(record["runtime"]["pid"]).kill()
    wait_for(
        lambda: (
            client.get("/api/v1/instances/" + record["id"]).json()["status"] == "failed"
        )
    )


def test_deleting_model_keeps_ready_instance_alive(client, cfg):
    record = ready(client, create(client))
    (cfg.models.directories[0] / "tiny" / "tiny.gguf").unlink()
    wait_for(lambda: not client.get("/api/v1/models").json()["models"])
    assert client.get("/api/v1/instances/" + record["id"]).json()["status"] == "ready"


def test_shutdown_stops_instances_restart_does_not_reload_and_refuses_reused_pid(cfg):
    app = create_app(cfg)
    with TestClient(app) as http:
        wait_for(lambda: http.get("/api/v1/models").json()["models"])
        record = ready(http, create(http))
    recovered_app = create_app(cfg)
    with TestClient(recovered_app) as http:
        restored = http.get("/api/v1/instances/" + record["id"]).json()
        assert restored["status"] == "stopped"
        assert http.get("/api/v1/instances").json()["instances"] == []
        assert http.get("/api/v1/nodes/local").json()["instance_count"] == 0
        assert [
            r["id"]
            for r in http.get("/api/v1/instances?include_stopped=true").json()[
                "instances"
            ]
        ] == [record["id"]]
        assert not psutil.pid_exists(record["runtime"]["pid"])
        assert (
            http.delete("/api/v1/instances/" + record["id"]).json()["status"]
            == "stopped"
        )
        # Persist an unrelated PID with a plausible-looking ready record.
        import asyncio, os
        from backend.app.repositories.instances import InstanceRepository

        forged = dict(record)
        forged["status"] = "ready"
        forged["runtime"]["pid"] = os.getpid()
        forged["runtime"]["process_created_at"] = psutil.Process().create_time() - 100
        forged["runtime"]["argv"] = ["unrelated"]
        asyncio.run(InstanceRepository(cfg.storage.database).save(forged))
    with TestClient(create_app(cfg)) as http:
        assert (
            http.get("/api/v1/instances/" + record["id"]).json()["status"] == "failed"
        )
        assert http.delete("/api/v1/instances/" + record["id"]).status_code == 200
        assert psutil.pid_exists(os.getpid())


def test_force_kill_after_graceful_shutdown_deadline(client):
    record = ready(client, create(client, 130))
    assert (
        client.delete("/api/v1/instances/" + record["id"]).json()["status"] == "stopped"
    )
    assert not psutil.pid_exists(record["runtime"]["pid"])
