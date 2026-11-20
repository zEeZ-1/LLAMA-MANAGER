import socket
import threading
import time
import httpx
import uvicorn
from backend.app.main import create_app
from .conftest import wait_for, create
from .test_instances import ready


def test_live_stream_is_incremental_and_disconnect_releases_capacity(cfg):
    cfg.instances.startup_timeout_seconds = 5
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(cfg), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started)
    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10
    ) as client:
        try:
            wait_for(lambda: client.get("/api/v1/models").json()["models"])
            instance = ready(client, create(client))
            endpoint = f"/api/v1/instances/{instance['id']}/chat/completions"
            payload = {"messages": [{"role": "user", "content": "stall"}]}
            started = time.monotonic()
            with client.stream("POST", endpoint, json=payload) as response:
                assert response.status_code == 200
                lines = response.iter_lines()
                first = next(lines)
                assert "Hello" in first and time.monotonic() - started < 1
                assert client.post(endpoint, json=payload).status_code == 429
            wait_for(
                lambda: client.get("/api/v1/health").json()["active_chats"] == 0,
                timeout=1.5,
            )
            response = client.post(
                endpoint,
                json={"messages": [{"role": "user", "content": "upstream-error"}]},
            )
            assert response.status_code == 502
            assert client.get("/api/v1/health").json()["active_chats"] == 0
            # Abort while upstream is still evaluating and has not returned response headers.
            import json

            raw = json.dumps(
                {"messages": [{"role": "user", "content": "delay-headers"}]}
            ).encode()
            sock = socket.create_connection(("127.0.0.1", port))
            sock.sendall(
                f"POST {endpoint} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n".encode()
                + raw
            )
            wait_for(lambda: client.get("/api/v1/health").json()["active_chats"] == 1)
            sock.close()
            wait_for(
                lambda: client.get("/api/v1/health").json()["active_chats"] == 0,
                timeout=1.5,
            )
        finally:
            for instance in client.get("/api/v1/instances").json()["instances"]:
                client.delete("/api/v1/instances/" + instance["id"])
            server.should_exit = True
            thread.join(timeout=10)
