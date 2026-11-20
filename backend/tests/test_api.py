def test_node_health_refresh_and_errors(client):
    node = client.get("/api/v1/nodes").json()["nodes"][0]
    assert node["id"] == "local" and node["memory_info"]["total"] > 0
    assert client.get("/api/v1/nodes/unknown").status_code == 404
    assert client.get("/api/v1/health").json()["runtime_available"]
    assert client.post("/api/v1/models/refresh").status_code == 202
    assert client.get("/api/v1/not-a-route").status_code == 404


def test_request_size_and_origin_boundaries(client):
    assert client.post("/api/v1/instances", content=b"x" * 5000).status_code == 413

    def chunks():
        yield b"x" * 3000
        yield b"x" * 3000

    assert client.post("/api/v1/instances", content=chunks()).status_code == 413
    assert (
        client.post(
            "/api/v1/models/refresh", headers={"Origin": "https://untrusted.example"}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/v1/health", headers={"Host": "untrusted.example"}).status_code
        == 400
    )
