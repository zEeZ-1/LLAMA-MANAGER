#!/usr/bin/env python3
"""Verify and package a myllama release without including model weights."""

import argparse
import gzip
import hashlib
import json
import re
import tarfile
from pathlib import Path


def verify(release: Path) -> tuple[int, int]:
    cache = {}
    checked = skipped = 0
    for line in (release / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        relative = Path(name.lstrip("*"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe checksum path: {name}")
        if "models" in relative.parts:
            skipped += 1
            continue
        path = release / relative
        if not path.resolve().is_relative_to(release):
            raise ValueError(f"File escapes release directory: {name}")
        stat = path.stat()
        key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        if key not in cache:
            with path.open("rb") as stream:
                cache[key] = hashlib.file_digest(stream, "sha256").hexdigest()
        if cache[key] != digest:
            raise ValueError(f"SHA256 mismatch: {name}")
        checked += 1
    if not checked:
        raise ValueError("SHA256SUMS contains no runtime files")
    return checked, skipped


def package(release: Path, output: Path) -> Path:
    name = release.name
    archive = output / f"{name}-runtime.tar.gz"
    checksum = archive.with_name(archive.name + ".sha256")
    if archive.exists() or checksum.exists():
        raise FileExistsError(f"Release artifacts already exist: {archive.name}")

    def include(info: tarfile.TarInfo):
        parts = Path(info.name).parts
        if "models" in parts[1:] or info.name.lower().endswith(".gguf"):
            return None
        if info.issym():
            target = release.parent / info.name
            if not target.resolve().is_relative_to(release):
                raise ValueError(f"Symlink escapes release directory: {info.name}")
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    output.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents replacing an immutable published archive.
    created_archive = created_checksum = False
    try:
        with archive.open("xb") as stream:
            created_archive = True
            with (
                gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0) as gz,
                tarfile.open(mode="w", fileobj=gz, format=tarfile.PAX_FORMAT) as tar,
            ):
                tar.add(release, arcname=name, filter=include)
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        with checksum.open("x") as stream:
            created_checksum = True
            stream.write(f"{digest}  {archive.name}\n")
    except BaseException:
        if created_archive:
            archive.unlink(missing_ok=True)
        if created_checksum:
            checksum.unlink(missing_ok=True)
        raise
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path, help="e.g. myllama/releases/myllama_v1")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    release = args.release.resolve()
    if not re.fullmatch(r"myllama_v[0-9]+(?:[._-][A-Za-z0-9]+)*", release.name):
        parser.error("Release directory must be named myllama_v<version>")
    manifest = json.loads((release / "manifest.json").read_text())
    if manifest.get("name") != release.name:
        parser.error("manifest.json name must match release directory")
    checked, skipped = verify(release)
    print(f"Verified {checked} runtime files; skipped {skipped} model checksum entries")
    if not args.verify_only:
        archive = package(release, (args.output_dir or release.parent).resolve())
        print(
            f"Created {archive} ({archive.stat().st_size} bytes), without model weights"
        )


if __name__ == "__main__":
    main()
