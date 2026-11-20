import os
from pathlib import Path


def supervised():
    unit = os.getenv("LIM_SYSTEMD_UNIT")
    if not unit:
        return False
    try:
        return unit + ".service" in Path("/proc/self/cgroup").read_text()
    except OSError:
        return False
