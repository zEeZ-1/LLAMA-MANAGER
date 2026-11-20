#!/usr/bin/env python3
"""Measure manager + optional dedicated browser tree, excluding llama-server only."""

import argparse
import json
import re
import time
from pathlib import Path
import psutil


def sample(roots):
    processes = {}
    roots = list(roots)
    # The default launcher waits outside the service cgroup. Count that Python
    # wrapper and systemd-run too; their RSS is part of management overhead.
    units = set()
    for pid in roots:
        try:
            units.update(
                re.findall(
                    r"llama-manager-[0-9a-f]{12}",
                    Path(f"/proc/{pid}/cgroup").read_text(),
                )
            )
        except OSError:
            pass
    if units:
        for proc in psutil.process_iter(["cmdline", "uids"]):
            try:
                args = proc.info["cmdline"] or []
                if (
                    args
                    and Path(args[0]).name == "systemd-run"
                    and any("--unit=" + unit in args for unit in units)
                ):
                    roots.append(proc.pid)
                    parent = proc.parent()
                    if parent and "backend.app" in parent.cmdline():
                        roots.append(parent.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    for pid in roots:
        try:
            root = psutil.Process(pid)
            for proc in [root, *root.children(recursive=True)]:
                processes[proc.pid] = proc
        except psutil.NoSuchProcess:
            continue
    included, excluded = [], []
    for proc in processes.values():
        try:
            args = proc.cmdline()
            name = Path(args[0]).name if args else proc.name()
            if name in {"llama-server", "llama-server.exe"}:
                excluded.append(proc.pid)
                continue
            info = proc.memory_full_info()
            included.append(
                {
                    "pid": proc.pid,
                    "name": name,
                    "rss": info.rss,
                    "uss": getattr(info, "uss", None),
                    "pss": getattr(info, "pss", None),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {
        "rss_bytes": sum(p["rss"] for p in included),
        "uss_bytes": sum(p["uss"] or 0 for p in included),
        "pss_bytes": sum(p["pss"] or 0 for p in included),
        "processes": included,
        "excluded_runtime_pids": excluded,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True, help="manager PID")
    parser.add_argument(
        "--browser-pid",
        type=int,
        help="dedicated browser PID, not your everyday browser",
    )
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--budget-mib", type=int, default=1024)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.seconds <= 0 or args.budget_mib <= 0:
        parser.error("seconds and budget must be positive")
    peak = {"rss_bytes": 0}
    roots = [args.pid] + ([args.browser_pid] if args.browser_pid else [])
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        current = sample(roots)
        if current["rss_bytes"] > peak["rss_bytes"]:
            peak = current
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    result = {
        "budget_bytes": args.budget_mib * 1024**2,
        "duration_seconds": args.seconds,
        "passed": peak["rss_bytes"] < args.budget_mib * 1024**2,
        "peak": peak,
    }
    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n")
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
