import io
import json

from PIL import Image
from .conftest import gguf, wait_for
from .test_instances import ready


def picture(color="red", size=(100, 80), format="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format=format)
    return stream.getvalue()


def add_vision(client, cfg):
    root = cfg.models.directories[0] / "vision"
    gguf(root / "main.gguf")
    gguf(
        root / "mmproj.gguf",
        metadata={"general.architecture": "clip", "clip.has_vision_encoder": True},
    )
    model = wait_for(
        lambda: next(
            (
                m
                for m in client.get("/api/v1/models").json()["models"]
                if m["relative_directory"] == "vision"
            ),
            None,
        )
    )
    response = client.post(
        "/api/v1/instances",
        json={
            "name": "image test",
            "model_id": model["id"],
            "config": {"modality": "image_text"},
        },
    )
    assert response.status_code == 202, response.text
    return ready(client, response.json())


def test_image_upload_cache_normalized_restore_and_limits(client, cfg):
    raw = picture()
    first = client.post("/api/v1/media/images", content=raw)
    assert first.status_code == 201, first.text
    media = first.json()
    assert media["mime_type"] == "image/png" and media["width"] == 100
    second = client.post("/api/v1/media/images", content=raw).json()
    assert second["cache_hit"] and second["sha256"] == media["sha256"]
    content = client.get(media["content_url"])
    assert content.headers["x-content-type-options"] == "nosniff"
    assert client.get(media["thumbnail_url"]).headers["content-type"] == "image/jpeg"
    restored = client.post(
        "/api/v1/media/images?representation=normalized", content=content.content
    ).json()
    assert restored["sha256"] == media["sha256"]
    assert (
        client.post(
            "/api/v1/media/images",
            content=b"not an image",
            headers={"content-type": "image/png"},
        ).status_code
        == 422
    )
    assert (
        client.post("/api/v1/media/images", content=picture(size=(8193, 1))).status_code
        == 422
    )
    assert (
        client.post("/api/v1/media/images", content=picture(format="GIF")).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/media/images", content=b"x" * (cfg.images.upload_bytes + 1)
        ).status_code
        == 413
    )
    assert not list((cfg.storage.media / "staging").iterdir())
    # Pinning prevents both explicit deletion and expiration GC during a model request.
    client.app.state.media.pins[media["id"]] = 1
    client.app.state.media.records[media["id"]]["expires_at"] = 0
    assert client.delete("/api/v1/media/images/" + media["id"]).status_code == 409
    assert client.get(media["content_url"]).status_code == 200
    client.app.state.media.pins.clear()
    assert client.get("/api/v1/media/images/" + media["id"]).status_code == 410
    assert client.delete("/api/v1/media/images/" + media["id"]).status_code == 200


def test_image_gateway_native_file_refs_and_capability(client, cfg):
    instance = add_vision(client, cfg)
    image = client.post("/api/v1/media/images", content=picture()).json()
    url = "/api/v1/instances/" + instance["id"] + "/chat/completions"
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image_id": image["id"]},
                {"type": "text", "text": "describe"},
            ],
        }
    ]
    response = client.post(url, json={"messages": messages, "stream": False})
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "Hello"
    assert (
        client.post(
            url,
            json={
                "messages": [{"role": "user", "content": [messages[0]["content"][0]]}],
                "stream": False,
            },
        ).status_code
        == 200
    )
    assert "[DONE]" in client.post(url, json={"messages": messages}).text
    assert client.app.state.media.active_images == 0 and not client.app.state.media.pins
    assert client.get("/api/v1/health").json()["active_chats"] == 0
    for content in (
        [{"type": "image_url", "image_url": {"url": "file:///etc/passwd"}}],
        [{"type": "image", "image_id": "../../etc/passwd"}],
    ):
        assert (
            client.post(
                url, json={"messages": [{"role": "user", "content": content}]}
            ).status_code
            == 422
        )
    assert (
        client.post(
            url,
            json={
                "messages": [{"role": "assistant", "content": messages[0]["content"]}]
            },
        ).status_code
        == 422
    )
    assert client.post(url, json={"messages": messages * 9}).status_code == 422
    record = client.app.state.manager.instances[instance["id"]]
    record["capabilities"]["image_input"] = False
    assert client.post(url, json={"messages": messages}).status_code == 409


def test_vision_capability_failure_is_reported_without_waiting_for_timeout(cfg):
    import asyncio
    import httpx
    import pytest
    from backend.app.runtime.llama_cpp import LlamaCppAdapter

    async def check():
        def response(request):
            return httpx.Response(200, json={"modalities": {"vision": False}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            adapter = LlamaCppAdapter(cfg, None, client)
            instance = {
                "config": {"modality": "image_text"},
                "runtime": {"endpoint": "http://127.0.0.1:12345"},
            }
            with pytest.raises(ValueError, match="vision_not_loaded"):
                await adapter.health_check(instance)

    asyncio.run(check())


def test_qwen35_package_creation_image_and_explicit_text_modes(client, cfg):
    root = cfg.models.directories[0] / "qwen-new"
    gguf(
        root / "main.gguf",
        metadata={
            "general.architecture": "qwen35",
            "general.name": "Qwen3.8-27B",
            "qwen35.embedding_length": 5120,
        },
    )
    gguf(
        root / "mmproj.gguf",
        metadata={
            "general.architecture": "clip",
            "general.name": "Qwen3.8-27B",
            "clip.has_vision_encoder": True,
            "clip.projector_type": "qwen3vl_merger",
            "clip.vision.projection_dim": 5120,
        },
    )
    model = wait_for(
        lambda: next(
            (
                m
                for m in client.get("/api/v1/models").json()["models"]
                if m["relative_directory"] == "qwen-new"
            ),
            None,
        )
    )
    assert model["kind"] == "image_text" and model["availability"] == "available"
    assert (
        model["runtime_support"] == "candidate" and not model["detection"]["verified"]
    )
    assert len(model["variants"]) == 1 and len(model["projectors"]) == 1
    for modality in ("image_text", "text"):
        created = client.post(
            "/api/v1/instances",
            json={
                "name": "Qwen architecture test",
                "model_id": model["id"],
                "config": {"modality": modality},
            },
        )
        assert created.status_code == 202, created.text
        instance = ready(client, created.json())
        argv = client.app.state.manager.get(instance["id"])["runtime"]["argv"]
        assert ("--mmproj" in argv) == (modality == "image_text")
        assert instance["capabilities"]["image_input"] == (modality == "image_text")
        if modality == "image_text":
            assert "--no-mmproj-offload" in argv
            image = client.post("/api/v1/media/images", content=picture()).json()
            content = [
                {"type": "image", "image_id": image["id"]},
                {"type": "text", "text": "describe"},
            ]
        else:
            content = "hello"
        response = client.post(
            "/api/v1/instances/" + instance["id"] + "/chat/completions",
            json={"messages": [{"role": "user", "content": content}], "stream": False},
        )
        assert response.status_code == 200, response.text
        client.delete("/api/v1/instances/" + instance["id"])


def test_qwen35_missing_projector_and_unknown_architecture_remain_blocked(client, cfg):
    root = cfg.models.directories[0]
    gguf(
        root / "qwen-missing" / "main.gguf", metadata={"general.architecture": "qwen35"}
    )
    gguf(
        root / "unrecognized" / "main.gguf",
        metadata={"general.architecture": "unknown_future_arch"},
    )
    models = wait_for(
        lambda: (
            ms
            if len(ms := client.get("/api/v1/models").json()["models"]) == 3
            else None
        )
    )
    missing = next(m for m in models if m["relative_directory"] == "qwen-missing")
    unknown = next(m for m in models if m["relative_directory"] == "unrecognized")
    assert missing["kind"] == "image_text" and missing["availability"] == "incomplete"
    assert unknown["availability"] == "unsupported"
    for model in (missing, unknown):
        response = client.post(
            "/api/v1/instances",
            json={
                "name": "blocked",
                "model_id": model["id"],
                "config": {"modality": "image_text"},
            },
        )
        assert response.status_code == 409


def test_package_variants_shards_legacy_and_unknown_format(client, cfg):
    root = cfg.models.directories[0]
    gguf(root / "loose.gguf")
    gguf(root / "multi" / "q4.gguf")
    gguf(root / "multi" / "q8.gguf")
    hf = root / "hf"
    hf.mkdir()
    (hf / "config.json").write_text('{"model_type":"example","vision_config":{}}')
    (hf / "model.safetensors").write_bytes(b"format only")
    models = wait_for(
        lambda: (
            client.get("/api/v1/models").json()
            if len(client.get("/api/v1/models").json()["models"]) == 3
            else None
        )
    )
    assert any(i["code"] == "legacy_layout" for i in models["layout_issues"])
    multi = next(m for m in models["models"] if m["relative_directory"] == "multi")
    assert len(multi["variants"]) == 2
    assert (
        client.post(
            "/api/v1/instances", json={"name": "choose", "model_id": multi["id"]}
        ).status_code
        == 409
    )
    hf_model = next(m for m in models["models"] if m["format"] == "hf_safetensors")
    assert hf_model["availability"] == "unsupported"
    assert (
        client.post(
            "/api/v1/instances", json={"name": "no", "model_id": hf_model["id"]}
        ).status_code
        == 409
    )
    for n in range(2):
        gguf(
            root / "sharded" / f"model-0000{n + 1}-of-00002.gguf",
            metadata={"split.count": 2, "split.no": n},
        )
    sharded = wait_for(
        lambda: next(
            (
                m
                for m in client.get("/api/v1/models").json()["models"]
                if m["relative_directory"] == "sharded"
            ),
            None,
        )
    )
    assert len(sharded["variants"]) == 1 and sharded["availability"] == "available"
    (root / "sharded" / "model-00002-of-00002.gguf").unlink()
    wait_for(
        lambda: (
            client.get("/api/v1/models/" + sharded["id"]).json().get("availability")
            == "incomplete"
        )
    )


def test_ambiguous_projector_and_asset_revalidation(client, cfg):
    instance = add_vision(client, cfg)
    model = next(
        m
        for m in client.get("/api/v1/models").json()["models"]
        if m["relative_directory"] == "vision"
    )
    assert "path" not in json.dumps(model) or '"path"' not in json.dumps(model)
    gguf(
        cfg.models.directories[0] / "vision" / "other-projector.gguf",
        metadata={"general.architecture": "clip", "clip.has_vision_encoder": True},
    )
    wait_for(
        lambda: (
            client.get("/api/v1/models/" + model["id"]).json().get("availability")
            == "needs_selection"
        )
    )
    assert (
        client.post(
            "/api/v1/instances",
            json={
                "name": "bad",
                "model_id": model["id"],
                "config": {"modality": "image_text"},
            },
        ).status_code
        == 409
    )
    assert client.get("/api/v1/instances/" + instance["id"]).json()["status"] == "ready"
    # An explicit binding can resolve ambiguity without accepting arbitrary paths.
    (cfg.models.directories[0] / "vision" / "model.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "variants": [{"entry": "main.gguf", "projector": "mmproj.gguf"}],
            }
        )
    )
    wait_for(
        lambda: (
            client.get("/api/v1/models/" + model["id"]).json().get("availability")
            == "available"
        )
    )


def test_metadata_stripped_and_normalized_cannot_bypass_it(client):
    image = Image.new("RGB", (80, 40), "red")
    exif = Image.Exif()
    exif[0x010E] = "private image description"
    data = io.BytesIO()
    image.save(data, format="PNG", exif=exif)
    rejected = client.post(
        "/api/v1/media/images?representation=normalized", content=data.getvalue()
    )
    assert rejected.status_code == 422
    accepted = client.post("/api/v1/media/images", content=data.getvalue()).json()
    assert not accepted["scaled"]
    with Image.open(
        io.BytesIO(client.get(accepted["content_url"]).content)
    ) as normalized:
        assert not normalized.getexif()


def test_layout_migration_preview_and_explicit_id_registration(cfg):
    import asyncio
    from scripts.preview_model_layout import preview, register
    from backend.app.repositories.manager import ManagerStore
    from backend.app.services.model_catalog import ModelCatalog

    root = cfg.models.directories[0]
    loose = root / "legacy.gguf"
    gguf(loose)
    plan = preview(cfg)
    move = plan["moves"][0]
    assert loose.is_file()
    target = root / move["target"]
    target.parent.mkdir()
    loose.rename(target)
    result = asyncio.run(register(cfg, plan))
    assert result["registered"] == 1

    async def resolve():
        store = ManagerStore(cfg.storage.database)
        await store.initialize()
        catalog = ModelCatalog(cfg.models, store)
        await catalog.initialize()
        try:
            await asyncio.sleep(0.08)
            await catalog.scan_all()
            model = catalog.get_model(move["old_id"])
            assert model["path"] == str(target)
            assert len(catalog.list_models()) == 2
        finally:
            await catalog.close()

    asyncio.run(resolve())


def test_live_image_disconnect_releases_pins_and_capacity(cfg):
    import socket
    import threading
    import httpx
    import uvicorn
    from backend.app.main import create_app

    cfg.instances.startup_timeout_seconds = 5
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(cfg)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started)
    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10
    ) as http:
        try:
            wait_for(lambda: http.get("/api/v1/models").json()["models"])
            instance = add_vision(http, cfg)
            image = http.post("/api/v1/media/images", content=picture()).json()
            url = "/api/v1/instances/" + instance["id"] + "/chat/completions"
            payload = {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image", "image_id": image["id"]},
                            {"type": "text", "text": "stall"},
                        ],
                    }
                ]
            }
            with http.stream("POST", url, json=payload) as response:
                assert response.status_code == 200
                lines = response.iter_lines()
                assert "Hello" in next(lines)
                assert app.state.media.active_images == 1
                assert (
                    http.delete("/api/v1/media/images/" + image["id"]).status_code
                    == 409
                )
                assert http.post(url, json=payload).status_code == 429
            wait_for(
                lambda: app.state.media.active_images == 0 and not app.state.media.pins,
                timeout=2,
            )
            assert http.get("/api/v1/health").json()["active_chats"] == 0
            assert http.delete("/api/v1/media/images/" + image["id"]).status_code == 200
        finally:
            for record in http.get("/api/v1/instances").json()["instances"]:
                http.delete("/api/v1/instances/" + record["id"])
            server.should_exit = True
            thread.join(timeout=10)


def test_multi_image_position_labels_keep_original_order_and_pins(client, cfg):
    from backend.app.schemas.instance import ChatRequest

    instance = add_vision(client, cfg)
    images = [
        client.post("/api/v1/media/images", content=picture(color)).json()
        for color in ("red", "blue")
    ]
    payload = ChatRequest.model_validate(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "compare"},
                        {"type": "image", "image_id": images[0]["id"]},
                        {"type": "image", "image_id": images[1]["id"]},
                    ],
                }
            ]
        }
    )

    async def inspect():
        async with client.app.state.media.prepare(payload, instance) as body:
            content = body["messages"][0]["content"]
            assert content[0] == {"type": "text", "text": "compare"}
            assert content[1]["text"].strip() == "First image:"
            assert content[3]["text"].strip() == "Second image:"
            assert images[0]["sha256"] in content[2]["image_url"]["url"]
            assert images[1]["sha256"] in content[4]["image_url"]["url"]
            assert len(client.app.state.media.pins) == 2
        assert not client.app.state.media.pins

    client.portal.call(inspect)
    argv = client.app.state.manager.get(instance["id"])["runtime"]["argv"]
    assert argv[argv.index("--image-max-tokens") + 1] == "1024"


def test_media_cache_ownership_preserves_unrelated_files(cfg):
    import pytest
    from fastapi.testclient import TestClient
    from backend.app.main import create_app

    images = cfg.storage.media / "images"
    images.mkdir(parents=True)
    unrelated = images / "user-photo.png"
    unrelated.write_bytes(b"do not remove")
    with pytest.raises(RuntimeError, match="not owned"):
        with TestClient(create_app(cfg)):
            pass
    assert unrelated.read_bytes() == b"do not remove"
    unrelated.unlink()
    with TestClient(create_app(cfg)) as client:
        unrelated.write_bytes(b"do not remove")
        client.portal.call(client.app.state.media.clean)
        assert unrelated.read_bytes() == b"do not remove"
        other = cfg.model_copy(deep=True)
        other.storage.database = cfg.storage.database.with_name("other.db")
        with pytest.raises(RuntimeError, match="another controller"):
            with TestClient(create_app(other)):
                pass
        assert unrelated.read_bytes() == b"do not remove"


def test_other_model_types_are_identified_without_loading_them(client, cfg):
    root = cfg.models.directories[0]
    gguf(root / "embedding" / "model.gguf", metadata={"general.architecture": "bert"})
    gguf(root / "audio" / "model.gguf")
    gguf(
        root / "audio" / "projector.gguf",
        metadata={"general.architecture": "clip", "clip.has_audio_encoder": True},
    )

    def found():
        models = {
            m["relative_directory"]: m
            for m in client.get("/api/v1/models").json()["models"]
        }
        return models if "embedding" in models and "audio" in models else None

    models = wait_for(found)
    assert models["embedding"]["kind"] == "other" and models["embedding"][
        "output_modalities"
    ] == ["embedding"]
    assert (
        models["audio"]["kind"] == "other"
        and "audio" in models["audio"]["input_modalities"]
    )
    for name in ("embedding", "audio"):
        assert models[name]["availability"] == "unsupported"
        assert (
            client.post(
                "/api/v1/instances", json={"name": name, "model_id": models[name]["id"]}
            ).status_code
            == 409
        )
