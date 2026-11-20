import os
import asyncio
import httpx
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
        if cfg.get("modality") == "image_text":
            if not model.get("projector_path"):
                raise ValueError("projector_mismatch: vision projector is required")
            cmd += [
                "--image-max-tokens",
                str(cfg.get("image_max_tokens", 1024)),
                "--mmproj",
                model["projector_path"],
                "--media-path",
                str(self.settings.storage.media / "images"),
            ]
            vision = cfg.get("vision_device_id", "cpu")
            if vision == "cpu":
                cmd += ["--no-mmproj-offload"]
            else:
                if (
                    not vision.startswith("local:cuda:")
                    or not vision.rsplit(":", 1)[1].isdigit()
                ):
                    raise ValueError(
                        "vision_device_invalid: choose CPU or a local CUDA device"
                    )
                cmd += [
                    "--mmproj-offload",
                    "--mmproj-device",
                    "CUDA" + vision.rsplit(":", 1)[1],
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
            if response.status_code != 200:
                return False
            if instance["config"].get("modality") == "image_text":
                props = await self.client.get(
                    instance["runtime"]["endpoint"] + "/props", timeout=2
                )
                if (
                    props.status_code != 200
                    or props.json().get("modalities", {}).get("vision") is not True
                ):
                    raise ValueError(
                        "vision_not_loaded: runtime did not confirm image input"
                    )
                instance["capabilities"] = {
                    "image_input": True,
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                }
            return True
        except httpx.HTTPError:
            return False

    async def cleanup_exited(self, instance):
        await self.supervisor.reap(instance["id"])

    async def maintain(self, instance):
        await asyncio.to_thread(self.supervisor.trim_log, instance["id"])
