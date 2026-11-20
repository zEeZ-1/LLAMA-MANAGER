#!/usr/bin/env python3
"""One-time local SSH password bridge; the password is never in argv/env/files."""

import os
import socket
import sys

if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]).lower()
    if "password" not in prompt and "密码" not in prompt:
        raise SystemExit(1)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(os.environ["LIM_ASKPASS_SOCKET"])
        value = b""
        while True:
            part = sock.recv(4096)
            if not part:
                break
            value += part
            if len(value) > 16384:
                raise SystemExit(1)
        sys.stdout.buffer.write(value + b"\n")
