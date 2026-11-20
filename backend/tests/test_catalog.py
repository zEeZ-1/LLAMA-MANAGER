import asyncio
from backend.app.services.gguf import read_metadata
from backend.app.services.model_catalog import ModelCatalog
from .conftest import gguf, wait_for


def test_metadata_does_not_read_weights_and_rejects_truncated_file(tmp_path):
    path = tmp_path / "model.gguf"
    gguf(path)
    assert read_metadata(path)["general.architecture"] == "llama"
    gguf(path, data_bytes=15)
    import pytest

    with pytest.raises(ValueError, match="incomplete"):
        read_metadata(path)


def test_watch_discovery_replacement_removal_and_symlink_boundary(
    client, cfg, tmp_path
):
    root = cfg.models.directories[0]
    original = client.get("/api/v1/models").json()["models"][0]
    assert "path" not in original and "fingerprint" not in original
    gguf(root / "nested" / "new.GGUF")
    wait_for(lambda: len(client.get("/api/v1/models").json()["models"]) == 2)
    gguf(root / "tiny" / "tiny.gguf", data_bytes=3)
    wait_for(lambda: len(client.get("/api/v1/models").json()["models"]) == 1)
    gguf(root / "tiny" / "tiny.gguf")
    wait_for(lambda: len(client.get("/api/v1/models").json()["models"]) == 2)
    assert any(
        m["id"] == original["id"] for m in client.get("/api/v1/models").json()["models"]
    )
    outside = tmp_path / "outside.gguf"
    gguf(outside)
    (root / "escape.gguf").symlink_to(outside)
    (root / "nested" / "new.GGUF").unlink()
    wait_for(lambda: len(client.get("/api/v1/models").json()["models"]) == 1)
    assert client.get("/api/v1/models").json()["models"][0]["filename"] == "tiny"


def test_stability_window_rejects_growing_and_truncated_files(cfg):
    async def scenario():
        cfg.models.watch = False
        cfg.models.stability_seconds = 0.1
        catalog = ModelCatalog(cfg.models)
        await catalog.scan_all()
        assert not catalog.models
        path = cfg.models.directories[0] / "tiny" / "tiny.gguf"
        gguf(path, data_bytes=1)
        await asyncio.sleep(0.12)
        await catalog.scan_all()
        assert not catalog.models
        await asyncio.sleep(0.12)
        await catalog.scan_all()
        assert not catalog.models and catalog.errors
        gguf(path)
        await catalog.scan_all()
        assert not catalog.models
        await asyncio.sleep(0.12)
        await catalog.scan_all()
        assert len(catalog.models) == 1

    asyncio.run(scenario())
