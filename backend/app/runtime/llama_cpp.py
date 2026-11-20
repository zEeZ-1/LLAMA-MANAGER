import os
import asyncio
from .base import RuntimeAdapter


class LlamaCppAdapter(RuntimeAdapter):
    def __init__(self, settings, supervisor, client):
        self.settings = settings
        self.supervisor = supervisor
        self.client = client

    def command(self, instance, model):
        cfg = instance["config"]
        cmd = [
            str(self.settings.runtime.llama_cpp.server_binary),
            "-m",
            model["path"],
            "--host",
            self.settings.instances.host,
            "--port",
            str(instance["runtime"]["internal_port"]),
            "--alias",
            instance["id"],
        ]
        for key, flag in {
            "context_size": "-c",
            "batch_size": "-b",
            "ubatch_size": "-ub",
            "parallel": "-np",
            "threads": "-t",
            "n_gpu_layers": "-ngl",
            "cache_type_k": "-ctk",
            "cache_type_v": "-ctv",
        }.items():
            cmd += [flag, str(cfg[key])]
        for key, enabled in cfg["extra_options"].items():
            if enabled:
                cmd += {
                    "flash_attn": ["--flash-attn", "on"],
                    "mlock": ["--mlock"],
                    "no_mmap": ["--no-mmap"],
                }[key]
        return cmd

    async def start(self, instance, model):
        binary = self.settings.runtime.llama_cpp.server_binary
        if not binary.is_file() or (os.name != "nt" and not os.access(binary, os.X_OK)):
            raise ValueError(
                "llama-server executable not found; configure LIM_LLAMA_SERVER"
            )
        return await self.supervisor.spawn(
            instance["id"], self.command(instance, model)
        )

    async def stop(self, instance):
        await self.supervisor.stop(instance)

    def get_status(self, instance):
        return self.supervisor.owned_process(instance["runtime"]) is not None

    async def health_check(self, instance):
        try:
            response = await self.client.get(
                instance["runtime"]["endpoint"] + "/health", timeout=2
            )
            return response.status_code == 200
        except Exception:
            return False

    async def cleanup_exited(self, instance):
        await self.supervisor.reap(instance["id"])

    async def maintain(self, instance):
        await asyncio.to_thread(self.supervisor.trim_log, instance["id"])
