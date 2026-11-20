"""Default V2 launcher: all inference processes share a systemd-owned lifecycle."""

import argparse
import hashlib
import os
import signal
import subprocess
import sys
import uvicorn
from .core.config import ROOT, load_settings
from .core.managed_service import supervised

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--direct",
        action="store_true",
        help="explicit test-only mode; no crash cleanup guarantee",
    )
    args = parser.parse_args()
    cfg = load_settings()
    if args.direct or supervised():
        if args.direct:
            print(
                "TEST MODE: normal shutdown cleans up; SIGKILL cleanup requires the default systemd launcher.",
                file=sys.stderr,
            )
        uvicorn.run(
            "backend.app.main:app",
            host=cfg.server.host,
            port=cfg.server.port,
            workers=1,
            timeout_graceful_shutdown=5,
        )
    else:
        unit = (
            "llama-manager-"
            + hashlib.sha256(str(cfg.storage.database).encode()).hexdigest()[:12]
        )
        command = [
            "systemd-run",
            "--user",
            "--collect",
            "--wait",
            "--pipe",
            "--service-type=exec",
            "--unit=" + unit,
            "--property=ExitType=main",
            "--property=KillMode=control-group",
            "--property=TimeoutStopSec=10",
            "--property=SendSIGKILL=yes",
            "--property=Restart=no",
            "--property=WorkingDirectory=" + str(ROOT),
            "--setenv=LIM_SYSTEMD_UNIT=" + unit,
        ]
        for key in [
            "LIM_CONFIG",
            "LIM_LLAMA_SERVER",
            "LIM_MODEL_DIRS",
            "LIM_DATABASE",
            "LIM_LOG_DIR",
            "SSH_AUTH_SOCK",
            "LD_LIBRARY_PATH",
            "PATH",
        ]:
            if key in os.environ:
                command += ["--setenv=" + key + "=" + os.environ[key]]
        command += [sys.executable, "-m", "backend.app"]

        def stop(signum, frame):
            subprocess.run(
                ["systemctl", "--user", "stop", unit + ".service"], check=False
            )

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        try:
            raise SystemExit(subprocess.call(command))
        except FileNotFoundError:
            raise SystemExit(
                "V2 requires systemd user services. Use --direct only for isolated tests."
            )
