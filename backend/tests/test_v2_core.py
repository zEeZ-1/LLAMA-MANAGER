import asyncio
import time
import pytest
from backend.app.nodes.telemetry import snapshot, public_snapshot
from backend.app.repositories.manager import ManagerStore
from backend.app.core.errors import ManagerError
from backend.app.schemas.instance import InstanceConfig
from backend.app.runtime.llama_cpp import LlamaCppAdapter


def test_jetson_na_is_shared_memory_not_gpu_failure():
    raw = {
        "hardware_model": "NVIDIA Jetson Orin Nano",
        "l4t": "R36",
        "architecture": "aarch64",
        "cpu_name": "aarch64",
        "cpu_count": "6",
        "cpu_total": "100",
        "cpu_idle": "50",
        "memory_total": "8000000000",
        "memory_available": "1000000000",
        "gpus": ["0, Orin (nvgpu), [N/A], [N/A], 0"],
    }
    first = snapshot(raw, "local")
    gpu = first["devices"][0]
    assert gpu["memory_kind"] == "shared" and gpu["memory_total"] is None
    assert gpu["compute_state"] == "unverified" and gpu["driver"] == "nvgpu"
    assert gpu["properties"]["utilization_percent"] == 0
    assert (
        first["cpu_info"]["name"] == "型号未提供"
        and first["cpu_info"]["usage_percent"] is None
    )
    second = snapshot({**raw, "cpu_total": "200", "cpu_idle": "100"}, "local", first)
    assert second["cpu_info"]["usage_percent"] == 50
    first["_sample_monotonic"] = time.monotonic() - 200
    assert public_snapshot(first)["telemetry"]["memory_available"]["stale"]


def test_na_on_discrete_gpu_is_unknown_not_assumed_shared():
    data = snapshot({"gpus": ["0, NVIDIA Example, [N/A], [N/A], [N/A]"]}, "remote")
    assert data["devices"][0]["memory_kind"] == "unknown"
    assert data["devices"][0]["properties"]["utilization_percent"] is None


def test_claims_are_atomic_and_multiworker_failure_rolls_back(cfg):
    async def run():
        from backend.app.repositories.instances import InstanceRepository

        await InstanceRepository(cfg.storage.database).initialize()
        store = ManagerStore(cfg.storage.database)
        await store.initialize()
        await store.reserve(["occupied"], "existing")
        with pytest.raises(ManagerError):
            await store.reserve(["new", "occupied"], "next")
        assert len(await store.claims()) == 1
        results = await asyncio.gather(
            store.reserve(["shared"], "a"),
            store.reserve(["shared"], "b"),
            return_exceptions=True,
        )
        assert sum(isinstance(r, ManagerError) for r in results) == 1
        await store.claim_state("existing", "quarantined")
        with pytest.raises(ManagerError):
            await store.reserve(["occupied"], "again")

    asyncio.run(run())


def test_remote_node_boundaries_and_secret_validation(client):
    base = {"name": "Worker", "address": "192.168.50.200", "username": "worker"}
    assert (
        client.post("/api/v1/nodes", json={**base, "address": "127.0.0.1"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/nodes", json={**base, "username": "worker; id"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/nodes", json={**base, "address": "https://example.com"}
        ).status_code
        == 422
    )
    node = client.post("/api/v1/nodes", json=base).json()
    assert client.post("/api/v1/nodes", json=base).status_code == 409
    response = client.post(
        f"/api/v1/nodes/{node['id']}/connect",
        json={"password": {"secret": "DO-NOT-ECHO-ME"}},
    )
    assert response.status_code == 422 and "DO-NOT-ECHO-ME" not in response.text
    assert client.delete("/api/v1/nodes/" + node["id"]).status_code == 200


def test_rpc_command_mapping_is_explicit_and_before_device_options(cfg):
    config = InstanceConfig.model_validate(
        {
            "n_gpu_layers": 99,
            "placement": {
                "mode": "rpc",
                "devices": [{"device_id": "gpu", "worker_id": "w", "weight": 1}],
            },
        }
    )
    instance = {
        "id": "i",
        "config": config.model_dump(),
        "runtime": {
            "internal_port": 18001,
            "placement": {
                "rpc_endpoints": ["192.168.50.12:50052", "192.168.50.11:50052"],
                "device_names": ["RPC1", "CUDA0", "RPC0"],
                "tensor_split": [2, 1, 3],
            },
        },
    }
    adapter = LlamaCppAdapter(cfg, None, None)
    argv = adapter.command(instance, {"path": "/allowed/model.gguf"})
    assert argv.index("--rpc") < argv.index("--device")
    assert argv[argv.index("--device") + 1] == "RPC1,CUDA0,RPC0"
    assert argv[argv.index("--tensor-split") + 1] == "2,1,3"
    assert argv[argv.index("--flash-attn") + 1] == "off"
    instance["config"]["extra_options"]["flash_attn"] = True
    enabled = adapter.command(instance, {"path": "/allowed/model.gguf"})
    assert enabled.count("--flash-attn") == 1
    assert enabled[enabled.index("--flash-attn") + 1] == "on"
    with pytest.raises(ValueError):
        InstanceConfig.model_validate({"placement": {"mode": "rpc", "devices": []}})
