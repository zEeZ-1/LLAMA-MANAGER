import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from .gguf import read_metadata
from .model_packages import detect, files_in, fingerprint, identifier, public_package

log = logging.getLogger(__name__)


class ModelCatalog:
    def __init__(self, settings, store=None):
        self.settings = settings
        self.store = store
        self.aliases = {}
        self.header_cache = {}
        self.layout_issues = []
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
        if self.store:
            self.aliases = {r["id"]: r for r in await self.store.all("model_aliases")}
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
        observed, models, errors, issues, aliases, header_cache = {}, {}, {}, [], {}, {}
        for root in self.settings.directories:
            for directory in sorted(root.iterdir()):
                if directory.name.startswith("."):
                    continue
                if not directory.is_dir() or directory.is_symlink():
                    if directory.suffix.lower() == ".gguf":
                        issues.append(
                            {
                                "relative_path": directory.name,
                                "code": "legacy_layout",
                                "message": "请将模型及投影整理到独立模型文件夹",
                            }
                        )
                    continue
                pid = identifier(f"package:{root}:{directory.relative_to(root)}")
                try:
                    paths = files_in(directory)
                    if not paths:
                        continue
                    stamps = {str(p.resolve()): fingerprint(p) for p in paths}
                    revision = identifier(str(sorted(stamps.items())))
                    previous = self.observed.get(str(directory))
                    since = previous[1] if previous and previous[0] == revision else now
                    observed[str(directory)] = (revision, since)
                    if now - since < self.settings.stability_seconds:
                        continue
                    old = self.models.get(pid)
                    if old and old["revision"] == revision:
                        models[pid] = old
                        for path in paths:
                            cached = self.header_cache.get(str(path.resolve()))
                            if cached:
                                header_cache[str(path.resolve())] = cached
                        continue
                    headers = {}
                    for path in paths:
                        if path.suffix.lower() != ".gguf":
                            continue
                        key = str(path.resolve())
                        cached = self.header_cache.get(key)
                        metadata = (
                            cached[1]
                            if cached and cached[0] == stamps[key]
                            else read_metadata(path, allow_split=True)
                        )
                        header_cache[key] = (stamps[key], metadata)
                        headers[path] = metadata
                    model = detect(root, directory, paths, revision, headers)
                    model["_files"] = stamps
                    model["_directory"] = str(directory)
                    models[pid] = model
                    for variant in model["variants"]:
                        first = Path(variant["path"])
                        old_id = identifier(f"local:{root}:{first.relative_to(root)}")
                        aliases[old_id] = dict(
                            id=old_id, package_id=pid, variant_id=variant["id"]
                        )
                except (OSError, ValueError, OverflowError, KeyError, TypeError) as exc:
                    errors[str(directory)] = ("", str(exc))
                    issues.append(
                        {
                            "relative_path": directory.name,
                            "code": "model_layout_invalid",
                            "message": str(exc)[:500],
                        }
                    )
        self.layout_issues = issues
        self.header_cache = header_cache
        self.aliases.update(aliases)
        return observed, models, errors

    async def scan_all(self):
        async with self.lock:
            self.observed, self.models, self.errors = await asyncio.to_thread(
                self._scan
            )
            if self.store:
                for alias in self.aliases.values():
                    await self.store.put("model_aliases", alias)
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

    def get_package(self, model_id):
        alias = self.aliases.get(model_id)
        model = self.models.get(alias["package_id"] if alias else model_id)
        if not model:
            raise ValueError(
                "Model package is unavailable, incomplete or still being copied"
            )
        return model

    def get_model(self, model_id, config=None):
        model = self.get_package(model_id)
        if model["availability"] != "available":
            raise ValueError(
                "Model package cannot be created: "
                + "; ".join(model["detection"]["reasons"])
            )
        if (
            config
            and config.model_revision
            and config.model_revision != model["revision"]
        ):
            raise ValueError("model_changed: refresh the selected model package")
        alias = self.aliases.get(model_id)
        variant_id = (config.variant_id if config else None) or (
            alias["variant_id"] if alias else model.get("default_variant_id")
        )
        variant = next((v for v in model["variants"] if v["id"] == variant_id), None)
        if variant is None:
            raise ValueError("model_selection_required: select a model variant")
        for key, stamp in model["_files"].items():
            path = Path(key)
            if (
                path.resolve(strict=True) != path
                or not path.is_relative_to(model["_directory"])
                or fingerprint(path) != stamp
            ):
                self.refresh()
                raise ValueError("model_changed: wait for catalog refresh")
        result = {
            **model,
            **variant,
            "id": model["id"],
            "name": model["name"],
            "variant_id": variant["id"],
            "projector_path": None,
        }
        if config and config.modality == "image_text":
            projector = next(
                (p for p in model["projectors"] if p["id"] == variant["projector_id"]),
                None,
            )
            if not projector:
                raise ValueError(
                    "projector_mismatch: model has no unique vision projector"
                )
            result["projector_path"] = projector["path"]
            result["projector_id"] = projector["id"]
        return result

    def list_models(self, node_id=None):
        return [
            self.public(m)
            for m in self.models.values()
            if node_id is None or node_id == m["node_id"]
        ]

    @staticmethod
    def public(model):
        return public_package(model)

    async def close(self):
        if self.observer:
            self.observer.stop()
            await asyncio.to_thread(self.observer.join, 3)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
