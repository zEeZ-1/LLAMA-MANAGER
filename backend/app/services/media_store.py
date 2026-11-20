"""Disk-backed, bounded attachments. Request pins protect native file reads."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import sys
import time
import uuid
from fastapi import HTTPException
from ..core.lock import ManagerLock

CACHE_FILE = re.compile(r"[a-f0-9]{64}(?:\.png|\.jpg|\.thumb\.jpg)")
STAGING_FILE = re.compile(r"[a-f0-9]{32}(?:\.part|\.png|\.jpg|\.thumb)")


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def error(code, message, status=422):
    return HTTPException(status, {"code": code, "message": message})


class MediaStore:
    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.root = cfg.storage.media
        self.images = self.root / "images"
        self.staging = self.root / "staging"
        self.records = {}
        self.pins = {}
        self.lock = asyncio.Lock()
        self.processor = asyncio.Lock()
        self.uploads = 0
        self.active_images = 0
        self.task = None
        self.namespace_lock = None

    async def initialize(self):
        owner = self.root / ".owner"
        expected = self.store.controller_id + "\n"
        if self.root.exists() and not owner.exists():
            for item in self.root.iterdir():
                if (
                    item.name not in {"images", "staging"}
                    or item.is_symlink()
                    or not item.is_dir()
                    or any(item.iterdir())
                ):
                    raise RuntimeError(
                        "Media directory contains files not owned by this manager; choose an empty dedicated directory"
                    )
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if owner.is_symlink():
            raise RuntimeError("Media ownership marker must be a regular file")
        if not owner.exists():
            with owner.open("x") as stream:
                os.chmod(owner, 0o600)
                stream.write(expected)
        if owner.read_text() != expected:
            raise RuntimeError("Media directory belongs to another controller")
        self.namespace_lock = ManagerLock(self.root / ".cache.lock")
        for p in (self.root, self.images, self.staging):
            if p.is_symlink():
                raise RuntimeError("Media directories must not be symlinks")
            p.mkdir(parents=True, exist_ok=True, mode=0o700)
            p.chmod(0o700)
        for p in self.staging.iterdir():
            if STAGING_FILE.fullmatch(p.name) and p.is_file() and not p.is_symlink():
                p.unlink()
        self.records = {r["id"]: r for r in await self.store.all("media_assets")}
        await self.clean()

        async def loop():
            while True:
                await asyncio.sleep(30)
                await self.clean()

        self.task = asyncio.create_task(loop())

    def file(self, record, thumbnail=False):
        if not re.fullmatch(r"[a-f0-9]{64}", record["sha256"]) or record[
            "suffix"
        ] not in {".png", ".jpg"}:
            raise error("media_expired", "图片缓存身份不合法", 410)
        name = record["sha256"] + (".thumb.jpg" if thumbnail else record["suffix"])
        path = self.images / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.resolve().parent != self.images
        ):
            raise error("media_expired", "图片暂存已过期，请恢复附件", 410)
        return path

    def get(self, identifier):
        record = self.records.get(identifier)
        if not record:
            raise error("media_not_found", "找不到图片附件", 404)
        if record["expires_at"] < time.time() and not self.pins.get(identifier):
            raise error("media_expired", "图片暂存已过期，请恢复附件", 410)
        self.file(record)
        return record

    def public(self, record):
        identifier = record["id"]
        return {
            k: v
            for k, v in record.items()
            if k not in {"suffix", "source_sha256", "recipe"}
        } | dict(
            content_url=f"/api/v1/media/images/{identifier}/content",
            thumbnail_url=f"/api/v1/media/images/{identifier}/thumbnail",
        )

    async def clean(self):
        async with self.lock:
            now = time.time()
            for identifier, r in list(self.records.items()):
                if r["expires_at"] < now and not self.pins.get(identifier):
                    self.records.pop(identifier)
                    await self.store.delete("media_assets", identifier)
            referenced = {
                r["sha256"] + suffix
                for r in self.records.values()
                for suffix in (r["suffix"], ".thumb.jpg")
            }
            for p in self.images.iterdir():
                if (
                    CACHE_FILE.fullmatch(p.name)
                    and p.name not in referenced
                    and p.is_file()
                    and not p.is_symlink()
                ):
                    p.unlink()

    async def delete(self, identifier):
        async with self.lock:
            if self.pins.get(identifier):
                raise error("media_busy", "图片正在推理中使用", 409)
            self.records.pop(identifier, None)
            await self.store.delete("media_assets", identifier)
        await self.clean()
        return {"status": "deleted"}

    async def upload(self, request, normalized=False):
        await self.clean()
        async with self.lock:
            if len(self.records) >= 4096:
                raise error(
                    "media_storage_full", "图片暂存记录已达上限，请清理旧附件", 507
                )
            used = sum(
                p.stat().st_size
                for folder in (self.images, self.staging)
                for p in folder.iterdir()
                if p.is_file()
            )
            if self.uploads >= 2:
                raise error("media_queue_full", "图片上传队列已满", 429)
            if (
                used + (self.uploads + 1) * self.cfg.images.upload_bytes
                > self.cfg.images.storage_bytes
            ):
                raise error("media_storage_full", "图片暂存空间不足", 507)
            self.uploads += 1
        token = uuid.uuid4().hex
        source = self.staging / (token + ".part")
        output = self.staging / token
        thumb = self.staging / (token + ".thumb")
        try:
            digest = hashlib.sha256()
            size = 0
            with source.open("xb") as f:
                os.chmod(source, 0o600)
                try:
                    async with asyncio.timeout(60):
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > self.cfg.images.upload_bytes:
                                raise error(
                                    "image_too_large", "图片超过上传大小上限", 413
                                )
                            digest.update(chunk)
                            await asyncio.to_thread(f.write, chunk)
                except TimeoutError:
                    raise error("media_upload_timeout", "图片上传超时", 408) from None
            source_hash = digest.hexdigest()
            recipe = f"v1:{self.cfg.images.normalized_side}:jpeg90:exif:strip"
            async with self.processor:
                async with self.lock:
                    cached = next(
                        (
                            r
                            for r in self.records.values()
                            if r["expires_at"] > time.time()
                            and r["recipe"] == recipe
                            and (
                                r["sha256"] == source_hash
                                if normalized
                                else r["source_sha256"] == source_hash
                            )
                        ),
                        None,
                    )
                    if cached:
                        self.file(cached)
                        cached["expires_at"] = time.time() + self.cfg.images.ttl_seconds
                        await self.store.put("media_assets", cached)
                        return self.public(cached) | {"cache_hit": True}
                cfg = self.cfg.images.model_dump() | dict(
                    source=str(source),
                    output=str(output),
                    thumbnail=str(thumb),
                    normalized=normalized,
                )
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "media_worker.py"),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    stdout, _ = await asyncio.wait_for(
                        proc.communicate(json.dumps(cfg).encode()), 10
                    )
                    if proc.returncode or len(stdout) > 4096:
                        raise error("image_invalid", "图片格式、尺寸或数据不合法")
                except TimeoutError:
                    raise error("image_invalid", "图片处理超时") from None
                finally:
                    if proc.returncode is None:
                        proc.kill()
                    await proc.wait()
                result = json.loads(stdout)
                canonical = output.with_suffix(result["suffix"])
                sha = await asyncio.to_thread(file_hash, canonical)
                record = result | dict(
                    id=str(uuid.uuid4()),
                    sha256=sha,
                    source_sha256=source_hash,
                    recipe=recipe,
                    size_bytes=canonical.stat().st_size,
                    expires_at=time.time() + self.cfg.images.ttl_seconds,
                    scaled=result.get("scaled", False),
                )
                async with self.lock:
                    if (
                        sum(
                            p.stat().st_size
                            for p in self.images.iterdir()
                            if p.is_file()
                        )
                        + record["size_bytes"]
                        + thumb.stat().st_size
                        > self.cfg.images.storage_bytes
                    ):
                        raise error("media_storage_full", "图片暂存空间不足", 507)
                    for src, dst in (
                        (canonical, self.images / (sha + result["suffix"])),
                        (thumb, self.images / (sha + ".thumb.jpg")),
                    ):
                        os.chmod(src, 0o600)
                        os.replace(src, dst)
                    await self.store.put("media_assets", record)
                    self.records[record["id"]] = record
                return self.public(record) | {"cache_hit": False}
        finally:
            for p in (
                source,
                thumb,
                output.with_suffix(".jpg"),
                output.with_suffix(".png"),
            ):
                p.unlink(missing_ok=True)
            async with self.lock:
                self.uploads -= 1

    @asynccontextmanager
    async def prepare(self, payload, instance):
        body = payload.model_dump(exclude_none=True)
        identifiers = []
        for message in body["messages"]:
            if isinstance(message["content"], list):
                identifiers.extend(
                    p["image_id"] for p in message["content"] if p["type"] == "image"
                )
        if not identifiers:
            yield body
            return
        if not instance.get("capabilities", {}).get("image_input"):
            raise error("image_input_unsupported", "当前实例不支持图片输入", 409)
        if len(identifiers) > 8:
            raise error("image_count_exceeded", "完整历史最多包含 8 张图片")
        async with self.lock:
            if self.active_images >= 1:
                raise error("image_queue_full", "已有图片请求正在处理", 429)
            records = {i: self.get(i) for i in identifiers}
            if sum(records[i]["size_bytes"] for i in identifiers) > 16 * 1024**2:
                raise error("image_too_large", "历史图片合计超过 16 MiB", 413)
            self.active_images += 1
            for i in set(identifiers):
                self.pins[i] = self.pins.get(i, 0) + 1
        try:
            for message in body["messages"]:
                if not isinstance(message["content"], list):
                    continue
                multiple = sum(p["type"] == "image" for p in message["content"]) > 1
                content = []
                image_index = 0
                for part in message["content"]:
                    if part["type"] != "image":
                        content.append(part)
                        continue
                    if multiple:
                        ordinal = ("First", "Second", "Third", "Fourth")[image_index]
                        content.append(
                            {"type": "text", "text": f"\n{ordinal} image:\n"}
                        )
                    image_index += 1
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "file://"
                                + self.file(records[part["image_id"]]).name
                            },
                        }
                    )
                message["content"] = content
            yield body
        finally:
            async with self.lock:
                self.active_images -= 1
                for i in set(identifiers):
                    self.pins[i] -= 1
                    if not self.pins[i]:
                        self.pins.pop(i)
                    if i in self.records:
                        self.records[i]["expires_at"] = (
                            time.time() + self.cfg.images.ttl_seconds
                        )
                        await self.store.put("media_assets", self.records[i])

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.namespace_lock:
            self.namespace_lock.close()
