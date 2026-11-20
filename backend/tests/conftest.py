import socket
import struct
import sys
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.app.core.config import Settings
from backend.app.main import create_app


def gguf(path: Path, data_bytes=16, metadata=None):
    def string(value):
        raw = value.encode()
        return struct.pack("<Q", len(raw)) + raw

    pairs = list(
        (
            {"general.name": "Tiny Test", "general.architecture": "llama"}
            | (metadata or {})
        ).items()
    )
    content = b"GGUF" + struct.pack("<IQQ", 3, 1, len(pairs))
    for key, value in pairs:
        if isinstance(value, bool):
            content += string(key) + struct.pack("<I?", 7, value)
        elif isinstance(value, int):
            content += string(key) + struct.pack("<II", 4, value)
        else:
            content += string(key) + struct.pack("<I", 8) + string(value)
    content += string("weight") + struct.pack("<IQIQ", 1, 4, 0, 0)
    content += b"\0" * (-len(content) % 32) + b"\0" * data_bytes
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def wait_for(fn, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Condition did not become true before timeout")


@pytest.fixture
def cfg(tmp_path):
    binary = tmp_path / "llama-server"
    source = Path(__file__).with_name("fake_runtime.py").read_text().split("\n", 1)[1]
    binary.write_text(f"#!{sys.executable}\n" + source)
    binary.chmod(0o755)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    settings = Settings.model_validate(
        {
            "runtime": {"llama_cpp": {"server_binary": str(binary)}},
            "models": {
                "directories": ["models"],
                "scan_interval_seconds": 0.15,
                "stability_seconds": 0.05,
            },
            "instances": {
                "port_range": {"start": port, "end": min(65535, port + 30)},
                "startup_timeout_seconds": 1.2,
                "shutdown_timeout_seconds": 0.3,
            },
            "limits": {"max_request_bytes": 4096, "max_concurrent_chats": 1},
        }
    ).resolve(tmp_path)
    gguf(settings.models.directories[0] / "tiny" / "tiny.gguf")
    return settings


@pytest.fixture
def client(cfg):
    app = create_app(cfg)
    with TestClient(app) as http:
        wait_for(lambda: http.get("/api/v1/models").json()["models"])
        yield http
        for instance in list(app.state.manager.instances.values()):
            if instance["status"] != "stopped":
                http.delete("/api/v1/instances/" + instance["id"])


def create(client, context=256):
    model = client.get("/api/v1/models").json()["models"][0]
    response = client.post(
        "/api/v1/instances",
        json={
            "name": "Test Instance",
            "model_id": model["id"],
            "config": {"context_size": context},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()
