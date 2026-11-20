#!/usr/bin/env python3
"""Compare native file/Base64 identities on an owned loopback vision instance."""

import base64
import hashlib
import json
from pathlib import Path
import time
import httpx


def main():
    root = Path("/tmp/lim-v3-qa")
    result = {
        "scope": "isolated native SmolVLM, file/Base64 compatibility comparison only; product path stays file references"
    }
    with httpx.Client(
        base_url="http://127.0.0.1:8018/api/v1", trust_env=False, timeout=180
    ) as c:
        model = next(
            m for m in c.get("/models").json()["models"] if m["name"] == "smolvlm500"
        )
        response = c.post(
            "/instances",
            json={
                "name": "V3 native image cache QA",
                "model_id": model["id"],
                "config": {
                    "modality": "image_text",
                    "context_size": 2048,
                    "batch_size": 512,
                    "ubatch_size": 512,
                    "threads": 4,
                },
            },
        )
        response.raise_for_status()
        instance = response.json()
        try:
            for _ in range(600):
                instance = c.get("/instances/" + instance["id"]).json()
                if instance["status"] == "ready":
                    break
                if instance["status"] == "failed":
                    raise AssertionError(instance["error_message"])
                time.sleep(0.2)
            assert instance["status"] == "ready"
            image = c.post(
                "/media/images", content=(root / "sample-a.png").read_bytes()
            ).json()
            download = c.get("http://127.0.0.1:8018" + image["content_url"])
            download.raise_for_status()
            binary = download.content
            prompt = "What is the main color? Answer with one color."
            file_request = {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image", "image_id": image["id"]},
                        ],
                    }
                ],
                "stream": False,
                "temperature": 0,
                "max_tokens": 30,
            }
            response = c.post(
                "/instances/" + instance["id"] + "/chat/completions", json=file_request
            )
            response.raise_for_status()
            file_response = response.json()
            data_url = (
                "data:"
                + image["mime_type"]
                + ";base64,"
                + base64.b64encode(binary).decode()
            )
            native_request = {
                "model": instance["id"],
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                "stream": False,
                "temperature": 0,
                "max_tokens": 30,
            }
            direct = f"http://127.0.0.1:{instance['runtime']['internal_port']}/v1/chat/completions"
            response = c.post(direct, json=native_request)
            if response.status_code >= 400:
                print(
                    "Native Base64 response:",
                    response.status_code,
                    response.text[:1500],
                    flush=True,
                )
            response.raise_for_status()
            base64_response = response.json()
            for value in (file_response, base64_response):
                assert "red" in value["choices"][0]["message"]["content"].lower()
            assert base64_response["timings"]["cache_n"] >= 100, base64_response[
                "timings"
            ]
            blue = c.post(
                "/media/images", content=(root / "sample-b.png").read_bytes()
            ).json()
            file_request["messages"][0]["content"][1]["image_id"] = blue["id"]
            response = c.post(
                "/instances/" + instance["id"] + "/chat/completions", json=file_request
            )
            response.raise_for_status()
            changed = response.json()
            assert "blue" in changed["choices"][0]["message"]["content"].lower()
            assert changed["timings"]["cache_n"] < base64_response["timings"]["cache_n"]
            result.update(
                normalized_sha256=hashlib.sha256(binary).hexdigest(),
                file_timings=file_response["timings"],
                base64_timings=base64_response["timings"],
                different_image_timings=changed["timings"],
                checks=[
                    "same image has reusable native media identity through file and Base64 inputs",
                    "changing image invalidates the image portion of the cached prefix",
                    "Base64 does not confer a cache advantage",
                ],
            )
        finally:
            c.delete("/instances/" + instance["id"]).raise_for_status()
    (root / "cache.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
