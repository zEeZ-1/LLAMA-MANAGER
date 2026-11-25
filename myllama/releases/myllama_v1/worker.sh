#!/usr/bin/env bash
set -euo pipefail
host=${1:?Usage: worker.sh BIND_IP [rpc-server options]}
shift
release_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runtime_libs="$release_dir/bin:${MYLLAMA_CUDA_LIB_DIR:-/usr/local/cuda-13.2/lib64}"
export LD_LIBRARY_PATH="$runtime_libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LD_PRELOAD="$release_dir/bin/overlay-hybrid.so"
export LLAMA_CACHE="${LLAMA_CACHE:-$HOME/llama-rpc/cache}"
export CUDA_VISIBLE_DEVICES=0

exec "$release_dir/bin/ggml-rpc-server" \
    --host "$host" --port "${MYLLAMA_RPC_PORT:-50162}" --device CUDA0 --cache "$@"
