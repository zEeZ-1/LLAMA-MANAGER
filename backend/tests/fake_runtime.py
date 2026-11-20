#!/usr/bin/env python3
"""Deterministic subprocess runtime; exercises real sockets, streaming and signals."""

import argparse
import json
import time
import signal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int)
parser.add_argument("-c", type=int, default=4096)
for flag in [
    "-ctk",
    "-ctv",
    "-m",
    "--host",
    "--alias",
    "-b",
    "-ub",
    "-np",
    "-t",
    "-ngl",
    "--mmproj",
    "--media-path",
]:
    parser.add_argument(flag)
args, _ = parser.parse_known_args()
if args.c == 130:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if args.c == 129:
    raise SystemExit(12)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        data = json.dumps(
            {"modalities": {"vision": bool(args.mmproj)}}
            if self.path == "/props"
            else {"status": "ok"}
        ).encode()
        self.send_response(503 if args.c == 128 else 200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][-1]["content"]
        if isinstance(prompt, list):
            from pathlib import Path

            for p in prompt:
                if p["type"] == "image_url":
                    assert p["image_url"]["url"].startswith("file://")
                    assert (Path(args.media_path) / p["image_url"]["url"][7:]).is_file()
            prompt = next(
                (p["text"] for p in prompt if p["type"] == "text"), "image-request"
            )
        if prompt == "delay-headers":
            time.sleep(5)
        if prompt == "upstream-error":
            self.send_response(500)
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b"oops")
        elif not body.get("stream"):
            data = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": "Hello"}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for content in ["Hello", " world"]:
                    data = json.dumps({"choices": [{"delta": {"content": content}}]})
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(5 if prompt == "stall" else 0.15)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True


ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
