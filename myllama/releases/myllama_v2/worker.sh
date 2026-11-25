#!/usr/bin/env bash
set -euo pipefail
release_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$release_dir/runtime.sh"
if [[ ${1:-} == --help || $# -eq 0 ]]; then
    printf '%s\n' 'Usage: ./worker.sh BIND_IP [--dry-run] [--] [ggml-rpc-server options]'
    printf '%s\n' 'Default RPC port: 50052. Set MYLLAMA_RPC_PORT to override it.'
    exit 0
fi
host=$1
shift
dry_run=0
if [[ ${1:-} == --dry-run ]]; then
    dry_run=1
    shift
fi
if [[ ${1:-} == -- ]]; then
    shift
fi
myllama_runtime worker
runtime_settings+=("CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-0}" "LLAMA_CACHE=${LLAMA_CACHE:-$HOME/llama-rpc/cache}")
myllama_launch "$release_dir/bin/ggml-rpc-server" --host "$host" --port "${MYLLAMA_RPC_PORT:-50052}" --device CUDA0 --cache "$@"
