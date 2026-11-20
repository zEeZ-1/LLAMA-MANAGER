#!/usr/bin/env python3
"""Create a local, verified RPC release package; never compiles on remote nodes."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import tarfile
import yaml

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime", type=Path, default=ROOT / "inference_backends/llama.cpp"
    )
    parser.add_argument(
        "--output", type=Path, default=ROOT / "inference_backends/rpc_packages"
    )
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "config/rpc_releases.yaml"
    )
    parser.add_argument("--id", default=None)
    parser.add_argument("--platform", choices=["jetson", "linux"], default="jetson")
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(runtime), "rev-parse", "HEAD"], text=True
    ).strip()
    if subprocess.check_output(
        ["git", "-C", str(runtime), "status", "--porcelain"], text=True
    ).strip():
        raise SystemExit(
            "Runtime checkout has changes; package only an explicitly verified clean build"
        )
    binary = runtime / "build/bin/ggml-rpc-server"
    if not binary.is_file():
        raise SystemExit("ggml-rpc-server is not built")
    cache = runtime / "build/CMakeCache.txt"
    if not cache.is_file() or any(
        flag + ":BOOL=ON" not in cache.read_text() for flag in ["GGML_CUDA", "GGML_RPC"]
    ):
        raise SystemExit(
            "This CUDA release builder requires a GGML_CUDA=ON, GGML_RPC=ON build"
        )
    version_header = (runtime / "ggml/include/ggml-rpc.h").read_text()
    version = ".".join(
        re.search(r"RPC_PROTO_" + part + r"_VERSION\s+(\d+)", version_header).group(1)
        for part in ["MAJOR", "MINOR", "PATCH"]
    )
    arch = platform.machine()
    identifier = args.id or f"{commit[:7]}-{arch}-{args.platform}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", identifier):
        raise SystemExit("Invalid release id")
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / (identifier + ".tar.gz")
    if archive.exists():
        raise SystemExit(
            "Release already exists; use another id rather than replacing a published package"
        )
    files = {}
    with tarfile.open(archive, "w:gz") as tar:
        selected = [binary, *sorted(binary.parent.glob("libggml*.so*"))]
        for source in selected:
            resolved = source.resolve()
            target = "bin/" + source.name
            with resolved.open("rb") as f:
                files[target] = hashlib.file_digest(f, "sha256").hexdigest()
            info = tar.gettarinfo(str(resolved), arcname=target)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with resolved.open("rb") as f:
                tar.addfile(info, f)
        tar.add(runtime / "LICENSE", arcname="LICENSE", recursive=False)
    with archive.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    record = dict(
        id=identifier,
        commit=commit,
        protocol_version=version,
        architecture=arch,
        platform=args.platform,
        glibc_min=platform.libc_ver()[1],
        archive=archive.name,
        sha256=digest,
        files=files,
        backend="cuda",
        supported_hardware="NVIDIA Orin sm_87"
        if args.platform == "jetson"
        else "Must be validated for the target CUDA device",
        license="MIT; retain upstream llama.cpp/ggml license; system CUDA libraries are not redistributed",
    )
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    content = (
        yaml.safe_load(args.manifest.read_text())
        if args.manifest.exists()
        else {"releases": []}
    )
    if any(r["id"] == identifier for r in content.get("releases", [])):
        raise SystemExit("Manifest id already exists")
    content.setdefault("releases", []).append(record)
    args.manifest.write_text(
        yaml.safe_dump(content, allow_unicode=True, sort_keys=False)
    )
    print(json.dumps({k: v for k, v in record.items() if k != "files"}, indent=2))


if __name__ == "__main__":
    main()
