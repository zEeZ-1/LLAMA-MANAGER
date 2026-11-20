#!/usr/bin/env python3
"""Native vision acceptance; optional existing idle real RPCs, never deploys them."""

import argparse
import hashlib
import json
from pathlib import Path
import time
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/tmp/lim-v3-qa"))
    parser.add_argument("--url", default="http://127.0.0.1:8018")
    parser.add_argument("--model", default="smolvlm500")
    parser.add_argument("--rpc", action="store_true")
    parser.add_argument(
        "--rpc-addresses",
        nargs=2,
        metavar=("NODE_A", "NODE_B"),
        help="Registered node addresses; required with --rpc",
    )
    parser.add_argument("--vision-device", default="cpu")
    args = parser.parse_args()
    if args.rpc and not args.rpc_addresses:
        parser.error("--rpc requires --rpc-addresses NODE_A NODE_B")
    assert args.url.startswith("http://127.0.0.1:") and args.root.is_relative_to("/tmp")
    result = {
        "scope": "native vision through isolated manager; existing RPCs retained"
        if args.rpc
        else "native single-machine CPU vision through isolated manager",
        "checks": [],
        "responses": [],
    }
    with httpx.Client(
        base_url=args.url + "/api/v1", trust_env=False, timeout=180
    ) as client:

        def call(method, path, body=None):
            r = client.request(method, path, json=body)
            r.raise_for_status()
            return r.json()

        model = next(
            m for m in call("GET", "/models")["models"] if m["name"] == args.model
        )
        config = {
            "modality": "image_text",
            "vision_device_id": args.vision_device,
            "context_size": 2048,
            "ubatch_size": 512,
            "batch_size": 512,
            "threads": 4,
        }
        before = {}
        remote_ids = []
        if args.rpc:
            nodes = call("GET", "/nodes")["nodes"]
            local = next(n for n in nodes if n["id"] == "local")
            devices = [
                {
                    "device_id": next(
                        d["id"] for d in local["devices"] if d["backend"] == "cuda"
                    ),
                    "weight": 1,
                    "worker_id": None,
                }
            ]
            for address in args.rpc_addresses:
                node = next(n for n in nodes if n.get("address") == address)
                worker = next(
                    w
                    for w in node["rpc_workers"]
                    if w["state"] == "ready"
                    and not w.get("claim")
                    and not w.get("client_count")
                )
                devices.append(
                    {
                        "device_id": worker["device_ids"][0],
                        "worker_id": worker["id"],
                        "weight": 1,
                    }
                )
                before[worker["id"]] = worker["process_identity"]
                remote_ids.append(node["id"])
            config.update(
                n_gpu_layers=99, placement={"mode": "rpc", "devices": devices}
            )
        instance = call(
            "POST",
            "/instances",
            {
                "name": "V3 native RPC acceptance"
                if args.rpc
                else "V3 native CPU acceptance",
                "model_id": model["id"],
                "config": config,
            },
        )
        try:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                instance = call("GET", "/instances/" + instance["id"])
                if instance["status"] == "failed":
                    raise AssertionError(instance["error_message"])
                if instance["status"] == "ready":
                    break
                time.sleep(0.2)
            assert (
                instance["status"] == "ready"
                and instance["capabilities"]["image_input"]
            ), instance
            result["instance"] = instance
            result["checks"].append(
                "main GGUF and projector loaded; real runtime confirms vision"
            )
            image_ids = []
            for name, color in [("sample-a.png", "red"), ("sample-b.png", "blue")]:
                response = client.post(
                    "/media/images", content=(args.root / name).read_bytes()
                )
                response.raise_for_status()
                image = response.json()
                image_ids.append(image["id"])
                message = {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "What is the main color? Answer with one color.",
                        },
                        {"type": "image", "image_id": image["id"]},
                    ],
                }
                t = time.monotonic()
                response = call(
                    "POST",
                    "/instances/" + instance["id"] + "/chat/completions",
                    {
                        "messages": [message],
                        "stream": False,
                        "temperature": 0,
                        "max_tokens": 40,
                    },
                )
                answer = response["choices"][0]["message"]["content"]
                assert color in answer.lower(), answer
                result["responses"].append(
                    {
                        "image": name,
                        "answer": answer,
                        "seconds": time.monotonic() - t,
                        "usage": response.get("usage"),
                        "timings": response.get("timings"),
                    }
                )
            result["checks"].append(
                "two different visual inputs independently identified correctly; filenames are not in prompts"
            )
            history = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "What is the main color? Answer with one color.",
                        },
                        {"type": "image", "image_id": image_ids[0]},
                    ],
                }
            ]
            previous = call(
                "POST",
                "/instances/" + instance["id"] + "/chat/completions",
                {
                    "messages": history,
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": 30,
                },
            )
            history += [
                previous["choices"][0]["message"],
                {
                    "role": "user",
                    "content": "What color was that image? Answer briefly.",
                },
            ]
            follow = call(
                "POST",
                "/instances/" + instance["id"] + "/chat/completions",
                {
                    "messages": history,
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": 40,
                },
            )
            assert "red" in follow["choices"][0]["message"]["content"].lower(), follow
            result["followup"] = {
                k: follow.get(k) for k in ("choices", "usage", "timings")
            }
            result["checks"].append(
                "image history preserved across turns; native cache metrics recorded independently of file cache"
            )
            payload = {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Describe these images briefly."}
                        ]
                        + [{"type": "image", "image_id": i} for i in image_ids],
                    }
                ],
                "max_tokens": 40,
                "temperature": 0,
            }
            with client.stream(
                "POST",
                "/instances/" + instance["id"] + "/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                stream = "".join(response.iter_text())
            assert "[DONE]" in stream, stream
            result["checks"].append(
                "native two-image ordered input and SSE complete; multi-image answer quality reported separately"
            )
            result["two_image_sse"] = stream
        finally:
            call("DELETE", "/instances/" + instance["id"])
        if args.rpc:
            after = {
                w["id"]: w["process_identity"]
                for n in call("GET", "/nodes")["nodes"]
                for w in n.get("rpc_workers", [])
                if w["id"] in before
            }
            assert before == after, {"before": before, "after": after}
            result["rpc_identities_unchanged"] = True
            result["checks"].append(
                "destroyed only QA llama-server; both original real RPC process identities unchanged"
            )
        result["asset_sha256"] = {
            p.name: hashlib.file_digest(p.open("rb"), "sha256").hexdigest()
            for p in (args.root / "models" / args.model).glob("*.gguf")
        }
    target = args.root / (
        "native-rpc-gpu.json"
        if args.rpc and args.vision_device != "cpu"
        else "native-rpc.json"
        if args.rpc
        else "native-cpu.json"
    )
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {"artifact": str(target), "checks": result["checks"]}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
