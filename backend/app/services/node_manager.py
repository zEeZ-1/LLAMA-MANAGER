import asyncio
import platform
import socket
import subprocess
import time
from datetime import datetime, timezone
import psutil


class LocalNodeProvider:
    id = "local"

    def __init__(self):
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.cached = None
        self.expires = 0
        self.lock = asyncio.Lock()

    def _sample(self):
        memory = psutil.virtual_memory()
        devices = []
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=True,
            )
            for line in result.stdout.splitlines():
                index, name, total, free, utilization = [
                    v.strip() for v in line.split(",")
                ]

                def number(value):
                    try:
                        return int(value) * 1024**2
                    except ValueError:
                        return None

                devices.append(
                    dict(
                        id=f"cuda:{index}",
                        name=name,
                        type="gpu",
                        index=int(index),
                        memory_total=number(total),
                        memory_free=number(free),
                        properties={
                            "utilization_percent": float(utilization)
                            if utilization.isdigit()
                            else None
                        },
                    )
                )
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        return dict(
            id=self.id,
            name="Local Node",
            node_type="local",
            hostname=socket.gethostname(),
            status="online",
            os=f"{platform.system()} {platform.release()}",
            architecture=platform.machine(),
            cpu_info={
                "name": platform.processor() or platform.machine(),
                "logical_count": psutil.cpu_count(),
                "physical_count": psutil.cpu_count(logical=False),
                "usage_percent": psutil.cpu_percent(),
            },
            memory_info={
                "total": memory.total,
                "available": memory.available,
                "used": memory.total - memory.available,
                "usage_percent": memory.percent,
            },
            devices=devices,
            created_at=self.created_at,
            updated_at=datetime.now(timezone.utc).isoformat(),
            manager_memory_bytes=psutil.Process().memory_info().rss,
        )

    async def get(self):
        async with self.lock:
            if time.monotonic() >= self.expires:
                self.cached = await asyncio.to_thread(self._sample)
                self.expires = time.monotonic() + 5
        return dict(self.cached)
