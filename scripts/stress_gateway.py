#!/usr/bin/env python3
"""Bounded streaming load against caller-selected test instances; creates no instances."""

import argparse
import asyncio
import json
import time
import httpx


async def run(args):
    deadline = time.monotonic() + args.seconds
    counts = {"completed": 0, "limited": 0, "errors": 0}
    async with httpx.AsyncClient(
        base_url=args.url.rstrip("/") + "/api/v1", trust_env=False, timeout=60
    ) as client:

        async def worker(index):
            instance = args.instance[index % len(args.instance)]
            while time.monotonic() < deadline:
                try:
                    async with client.stream(
                        "POST",
                        f"/instances/{instance}/chat/completions",
                        json={
                            "messages": [
                                {"role": "user", "content": "Tell a short story."}
                            ],
                            "stream": True,
                            "max_tokens": 24,
                        },
                    ) as response:
                        if response.status_code == 429:
                            counts["limited"] += 1
                            await asyncio.sleep(0.05)
                            continue
                        response.raise_for_status()
                        done = False
                        async for line in response.aiter_lines():
                            if line == "data: [DONE]":
                                done = True
                        counts["completed" if done else "errors"] += 1
                except httpx.HTTPError:
                    counts["errors"] += 1
                    await asyncio.sleep(0.1)

        await asyncio.gather(*(worker(i) for i in range(args.concurrency)))
    print(
        json.dumps({"seconds": args.seconds, "concurrency": args.concurrency, **counts})
    )
    if counts["errors"]:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--instance", action="append", required=True)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--concurrency", type=int, default=7)
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 32 or args.seconds <= 0:
        parser.error("concurrency must be 1..32 and seconds must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
