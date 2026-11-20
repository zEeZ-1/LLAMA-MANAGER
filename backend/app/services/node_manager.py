import asyncio
import subprocess
import time
from pathlib import Path
import psutil
from ..nodes.telemetry import now, parse_inventory, public_snapshot, snapshot

INVENTORY_SCRIPT = Path(__file__).resolve().parents[1] / "remote/inventory.sh"


class LocalNodeProvider:
    id = "local"

    def __init__(self):
        self.created_at = now()
        self.cached = None
        self.expires = 0
        self.lock = asyncio.Lock()

    def _sample(self):
        try:
            result = subprocess.run(
                ["sh", str(INVENTORY_SCRIPT)],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            data = dict(self.cached) if self.cached else snapshot({}, "local")
            data["last_error"] = {
                "code": "inventory_failed",
                "message": "本机采样失败，显示最近数据；请刷新后核对",
            }
            data["_sample_monotonic"] = 0
            return data
        data = snapshot(parse_inventory(result.stdout), "local", self.cached)
        data["manager_memory_bytes"] = psutil.Process().memory_info().rss
        data["created_at"] = self.created_at
        return data

    async def get(self):
        async with self.lock:
            if time.monotonic() >= self.expires:
                self.cached = await asyncio.to_thread(self._sample)
                self.expires = time.monotonic() + 5
        return public_snapshot(self.cached)
