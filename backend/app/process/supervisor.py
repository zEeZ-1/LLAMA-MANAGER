import asyncio
import os
from pathlib import Path
import psutil


class ProcessSupervisor:
    def __init__(self, settings):
        self.settings = settings
        self.children = {}

    async def spawn(self, instance_id, argv):
        self.settings.storage.logs.mkdir(parents=True, exist_ok=True)
        filename = self.settings.storage.logs / f"{instance_id}.log"
        # Direct append-mode output allows inference processes to survive manager restart.
        with filename.open("ab", buffering=0) as output:
            env = {
                k: v for k, v in os.environ.items() if not k.startswith("LLAMA_ARG_")
            }
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=output,
                stderr=output,
                stdin=asyncio.subprocess.DEVNULL,
                env=env,
                start_new_session=os.name != "nt",
            )
        self.children[instance_id] = proc
        try:
            identity = psutil.Process(proc.pid).create_time()
        except psutil.NoSuchProcess:
            await proc.wait()
            raise RuntimeError(
                f"llama-server exited immediately (code {proc.returncode}); see instance log"
            )
        return {"pid": proc.pid, "process_created_at": identity, "argv": argv}

    def owned_process(self, runtime):
        try:
            proc = psutil.Process(runtime["pid"])
            if abs(proc.create_time() - runtime["process_created_at"]) > 0.01:
                return None
            args = proc.cmdline()
            expected = runtime["argv"]
            if args[-len(expected[1:]) :] != expected[1:]:
                return None
            if str(Path(expected[0]).resolve()) not in [
                str(Path(a).resolve()) for a in args[:2]
            ]:
                return None
            if proc.status() == psutil.STATUS_ZOMBIE:
                return None
            return proc
        except (psutil.Error, KeyError, TypeError):
            return None

    async def stop(self, instance):
        runtime = instance["runtime"]
        proc = self.owned_process(runtime)
        if not proc:
            await self.reap(instance["id"])
            return
        try:
            proc.terminate()
        except psutil.NoSuchProcess:
            pass
        deadline = (
            asyncio.get_running_loop().time()
            + self.settings.instances.shutdown_timeout_seconds
        )
        while (
            self.owned_process(runtime) and asyncio.get_running_loop().time() < deadline
        ):
            await asyncio.sleep(0.1)
        if self.owned_process(runtime):
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                pass
            for _ in range(30):
                if not self.owned_process(runtime):
                    break
                await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Process did not exit after force kill")
        await self.reap(instance["id"])

    async def reap(self, instance_id):
        if proc := self.children.pop(instance_id, None):
            await proc.wait()

    def trim_log(self, instance_id):
        filename = self.settings.storage.logs / f"{instance_id}.log"
        try:
            if filename.stat().st_size > self.settings.limits.max_log_bytes:
                # The writer uses O_APPEND. Keep a bounded tail, then reuse its file descriptor.
                with filename.open("r+b") as f:
                    f.seek(
                        -min(1024 * 1024, self.settings.limits.max_log_bytes // 2), 2
                    )
                    tail = f.read()
                    f.seek(0)
                    f.truncate()
                    f.write(b"[manager: earlier log output truncated]\n" + tail)
        except OSError:
            pass
