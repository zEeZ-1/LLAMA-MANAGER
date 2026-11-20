import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from .gguf import read_metadata

log = logging.getLogger(__name__)


class ModelCatalog:
    def __init__(self, settings):
        self.settings = settings
        self.models = {}
        self.observed = {}
        self.errors = {}
        self.last_scan = None
        self.event = asyncio.Event()
        self.lock = asyncio.Lock()
        self.task = None
        self.observer = None

    async def initialize(self):
        for directory in self.settings.directories:
            directory.mkdir(parents=True, exist_ok=True)
        await self.scan_all()
        loop = asyncio.get_running_loop()
        if self.settings.watch:
            catalog = self

            class Handler(FileSystemEventHandler):
                def on_any_event(self, event):
                    # Ignore read/open notifications from our own metadata parser.
                    if event.event_type in {
                        "created",
                        "modified",
                        "deleted",
                        "moved",
                        "closed",
                    }:
                        loop.call_soon_threadsafe(catalog.event.set)

            try:
                self.observer = Observer()
                for directory in self.settings.directories:
                    self.observer.schedule(
                        Handler(), str(directory), recursive=self.settings.recursive
                    )
                self.observer.start()
            except OSError:
                log.exception(
                    "File watcher unavailable; periodic scanning remains active"
                )
                if self.observer:
                    self.observer.stop()
                self.observer = None
        self.task = asyncio.create_task(self._run())

    def _scan(self):
        now = time.monotonic()
        observed, models, errors = {}, {}, {}
        for root in self.settings.directories:
            for path in root.rglob("*") if self.settings.recursive else root.glob("*"):
                if path.suffix.lower() != ".gguf":
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(root) or not resolved.is_file():
                        continue
                    stat = resolved.stat()
                    if stat.st_size == 0:
                        continue
                    key = str(resolved)
                    fingerprint = f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"
                    previous = self.observed.get(key)
                    since = (
                        previous[1] if previous and previous[0] == fingerprint else now
                    )
                    observed[key] = (fingerprint, since)
                    relative = str(path.relative_to(root))
                    model_id = hashlib.sha256(
                        f"local:{root}:{relative}".encode()
                    ).hexdigest()[:24]
                    if now - since < self.settings.stability_seconds:
                        continue
                    old = self.models.get(model_id)
                    if old and old["fingerprint"] == fingerprint:
                        models[model_id] = old
                        continue
                    if key in self.errors and self.errors[key][0] == fingerprint:
                        errors[key] = self.errors[key]
                        continue
                    metadata = read_metadata(resolved)
                    models[model_id] = dict(
                        id=model_id,
                        name=metadata.get("general.name") or path.stem,
                        filename=path.name,
                        path=key,
                        relative_path=relative,
                        format="gguf",
                        size_bytes=stat.st_size,
                        architecture=metadata.get("general.architecture"),
                        metadata=metadata,
                        node_id="local",
                        modified_at=datetime.fromtimestamp(
                            stat.st_mtime, timezone.utc
                        ).isoformat(),
                        fingerprint=fingerprint,
                    )
                except (OSError, ValueError, OverflowError) as exc:
                    errors[str(path.resolve())] = (
                        locals().get("fingerprint", ""),
                        str(exc),
                    )
        return observed, models, errors

    async def scan_all(self):
        async with self.lock:
            self.observed, self.models, self.errors = await asyncio.to_thread(
                self._scan
            )
            self.last_scan = datetime.now(timezone.utc).isoformat()

    async def _run(self):
        while True:
            try:
                # A quick second observation admits stable newly copied files.
                pending = len(self.observed) > len(self.models) + len(self.errors)
                timeout = (
                    self.settings.stability_seconds
                    if pending
                    else self.settings.scan_interval_seconds
                )
                await asyncio.wait_for(self.event.wait(), timeout)
                self.event.clear()
                await asyncio.sleep(self.settings.stability_seconds)
            except TimeoutError:
                pass
            try:
                await self.scan_all()
            except Exception:
                log.exception("Model scan failed; retaining previous catalog")

    def refresh(self):
        self.event.set()

    def get_model(self, model_id):
        model = self.models.get(model_id)
        if not model:
            raise ValueError("Model is unavailable or still being copied")
        path = Path(model["path"])
        resolved = path.resolve(strict=True)
        if resolved != path or not any(
            resolved.is_relative_to(root) for root in self.settings.directories
        ):
            raise ValueError("Model path is outside configured directories")
        stat = path.stat()
        fingerprint = f"{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}:{stat.st_ctime_ns}"
        if fingerprint != model["fingerprint"]:
            self.refresh()
            raise ValueError("Model changed; wait for catalog refresh")
        return model

    def list_models(self, node_id=None):
        return [
            self.public(m)
            for m in self.models.values()
            if node_id is None or node_id == m["node_id"]
        ]

    @staticmethod
    def public(model):
        return {k: v for k, v in model.items() if k not in {"path", "fingerprint"}}

    async def close(self):
        if self.observer:
            self.observer.stop()
            await asyncio.to_thread(self.observer.join, 3)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
