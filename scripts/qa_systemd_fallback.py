#!/usr/bin/env python3
"""Verify systemd SIGKILL fallback for a managed child which ignores SIGTERM."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

import psutil


def main():
    unit = "llama-manager-qa-cleanup-" + uuid.uuid4().hex[:12] + ".service"
    with tempfile.TemporaryDirectory(prefix="lim-systemd-qa-") as directory:
        ready = Path(directory) / "ready"
        child = Path(directory) / "child.py"
        parent = Path(directory) / "parent.py"
        child.write_text(
            "import os,signal,time\nfrom pathlib import Path\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\nPath("
            + repr(str(ready))
            + ").write_text(str(os.getpid()))\nwhile True: time.sleep(1)\n"
        )
        parent.write_text(
            "import subprocess,time,sys\nsubprocess.Popen([sys.executable,"
            + repr(str(child))
            + "],start_new_session=True)\nwhile True: time.sleep(1)\n"
        )
        try:
            subprocess.run(
                [
                    "systemd-run",
                    "--user",
                    "--unit=" + unit,
                    "--service-type=exec",
                    "--property=ExitType=main",
                    "--property=KillMode=control-group",
                    "--property=TimeoutStopSec=10",
                    "--property=SendSIGKILL=yes",
                    "--property=Restart=no",
                    sys.executable,
                    str(parent),
                ],
                check=True,
                capture_output=True,
            )
            deadline = time.monotonic() + 15
            while not ready.exists():
                assert time.monotonic() < deadline, "test child did not start"
                time.sleep(0.1)
            child_pid = int(ready.read_text())
            main_pid = int(
                subprocess.check_output(
                    ["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"]
                )
            )
            assert unit in Path(f"/proc/{child_pid}/cgroup").read_text()
            started = time.monotonic()
            os.kill(main_pid, signal.SIGKILL)
            while (
                psutil.pid_exists(child_pid)
                and psutil.Process(child_pid).status() != psutil.STATUS_ZOMBIE
            ):
                assert time.monotonic() - started < 15, (
                    "systemd failed to kill the stubborn child"
                )
                time.sleep(0.1)
            print(
                json.dumps(
                    {
                        "checks": [
                            "same cgroup despite setsid",
                            "SIGKILL parent",
                            "child ignored SIGTERM",
                            "systemd forced child exit after TimeoutStopSec",
                        ],
                        "elapsed_seconds": time.monotonic() - started,
                        "timeout_stop_seconds": 10,
                    }
                ),
                flush=True,
            )
        finally:
            subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
            subprocess.run(
                ["systemctl", "--user", "reset-failed", unit], capture_output=True
            )


if __name__ == "__main__":
    main()
