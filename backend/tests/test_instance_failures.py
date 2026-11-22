import json

from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.runtime.failures import describe_failure
from .conftest import create, wait_for


def test_rpc_kv_failure_reaches_list_detail_and_survives_restart(cfg):
    with TestClient(create_app(cfg)) as client:
        wait_for(lambda: client.get("/api/v1/models").json()["models"])
        record = create(client, 131)
        url = "/api/v1/instances/" + record["id"]
        failed = wait_for(
            lambda: r if (r := client.get(url).json())["status"] == "failed" else None
        )
        error = failed["error_detail"]
        assert error["code"] == "runtime_memory_allocation_failed"
        assert error["exit_code"] == 17
        assert (
            "192.0.2.10:50052" in error["message"]
            and "960.00 MiB" in error["message"]
            and "KV 缓存" in error["message"]
        )
        assert failed["error_message"] == error["message"]
        assert "failed to allocate buffer for kv cache" in error["log_excerpt"]
        assert "/private/models" not in json.dumps(failed)
        listed = client.get("/api/v1/instances").json()["instances"]
        assert listed[0]["error_detail"] == error
    with TestClient(create_app(cfg)) as client:
        assert client.get(url).json()["error_detail"] == error


def test_historical_failure_is_enriched_without_reloading_model(cfg):
    import asyncio
    from backend.app.repositories.instances import InstanceRepository

    with TestClient(create_app(cfg)) as client:
        wait_for(lambda: client.get("/api/v1/models").json()["models"])
        instance = create(client, 131)
        url = "/api/v1/instances/" + instance["id"]
        old = wait_for(
            lambda: r if (r := client.get(url).json())["status"] == "failed" else None
        )
    old.pop("error_detail")
    old["error_message"] = "llama-server exited unexpectedly; inspect instance log"
    asyncio.run(InstanceRepository(cfg.storage.database).save(old))
    with TestClient(create_app(cfg)) as client:
        restored = client.get(url).json()
        assert restored["status"] == "failed"
        assert "960.00 MiB" in restored["error_message"]
        assert restored["runtime"]["pid"] == old["runtime"]["pid"]


def test_error_excerpt_ignores_warnings_is_bounded_and_removes_private_data():
    text = (
        "W model has unused tensor blk.64.weight -- ignoring\n"
        + "\x1b[31mE error loading model '/private/models/model.gguf': missing tensor weight\x1b[0m\n"
        + "E password=secret --api-key api-secret authorization: Bearer bearer-secret\n"
        + ("E " + "x" * 2000 + "\n") * 8
    )
    error = describe_failure(text, "fallback")
    assert "missing tensor weight" in error["message"]
    serialized = json.dumps(error)
    assert "/private/models" not in serialized and "unused tensor" not in serialized
    assert "secret" not in serialized and "\\u001b" not in serialized
    assert len(error["log_excerpt"]) <= 4000


def test_no_native_error_preserves_timeout_and_reports_unknown_exit():
    error = describe_failure(
        "W informational warning", "Model startup timed out", "startup_timeout"
    )
    assert error == {
        "code": "startup_timeout",
        "message": "Model startup timed out",
        "log_excerpt": "",
    }
    assert describe_failure("", "process exited", exit_code=-9)["exit_code"] == -9
    assert (
        describe_failure("E bind: Address already in use", "fallback")["code"]
        == "runtime_port_in_use"
    )
    allocation = describe_failure(
        "E failed to allocate CUDA0 buffer of size 1048576", "fallback"
    )
    assert (
        allocation["code"] == "runtime_memory_allocation_failed"
        and "1.0 MiB" in allocation["message"]
    )
