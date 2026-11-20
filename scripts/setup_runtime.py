#!/usr/bin/env python3
"""Clone the verified upstream commit or link a caller-selected local checkout."""

import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VERIFIED_COMMIT = "9214bdb554544c35e4a9e2c2e8b75d8cfee330ab"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="link an existing llama.cpp checkout (no source changes)",
    )
    args = parser.parse_args()
    target = ROOT / "inference_backends/llama.cpp"
    if target.exists() or target.is_symlink():
        parser.error(f"{target} already exists; keep it or configure LIM_LLAMA_SERVER")
    target.parent.mkdir(exist_ok=True)
    if args.source:
        source = args.source.resolve(strict=True)
        if not (source / "CMakeLists.txt").is_file():
            parser.error("source must be a llama.cpp checkout")
        target.symlink_to(
            os.path.relpath(source, target.parent), target_is_directory=True
        )
    else:
        target.mkdir()
        subprocess.run(["git", "init", str(target)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(target),
                "remote",
                "add",
                "origin",
                "https://github.com/ggml-org/llama.cpp.git",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(target), "fetch", "--depth=1", "origin", VERIFIED_COMMIT],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(target), "checkout", "--detach", "FETCH_HEAD"], check=True
        )
    print(f"Runtime source: {target}")
    print(
        "Build with: cmake -S inference_backends/llama.cpp -B inference_backends/llama.cpp/build -DGGML_CUDA=OFF"
    )
    print(
        "Then: cmake --build inference_backends/llama.cpp/build --target llama-server -j 2"
    )


if __name__ == "__main__":
    main()
