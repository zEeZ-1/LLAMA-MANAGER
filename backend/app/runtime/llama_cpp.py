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
        placement = instance["runtime"].get("placement")
        if placement:
            cmd += [
                "--rpc",
                ",".join(placement["rpc_endpoints"]),
                "--device",
                ",".join(placement["device_names"]),
                "--split-mode",
                "layer",
                "--tensor-split",
                ",".join(str(v) for v in placement["tensor_split"]),
                "--fit",
                "off",
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
        # The checkbox is a boolean. Passing nothing would select upstream's
        # automatic mode, which can choose an unsupported remote CUDA kernel.
        cmd += [
            "--flash-attn",
            "on" if cfg["extra_options"].get("flash_attn", False) else "off",
        ]
        for key, enabled in cfg["extra_options"].items():
            if enabled and key != "flash_attn":
                cmd += {
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
        if instance["runtime"].get("placement"):
            proc = await asyncio.create_subprocess_exec(
                str(binary),
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                output, _ = await asyncio.wait_for(proc.communicate(), 5)
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            expected = self.settings.rpc.expected_commit[:7]
            if expected not in output.decode(errors="replace"):
                raise ValueError(
                    "Controller llama-server commit does not match the verified RPC package"
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
