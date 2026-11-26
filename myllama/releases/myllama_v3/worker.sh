#!/usr/bin/env bash
set -euo pipefail
if [[ $# -eq 0 || $1 == --help ]]; then
    echo 'Usage: ./worker.sh BIND_IP [ggml-rpc-server options]'
    exit 0
fi
bind_ip=$1
shift
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
export LLAMA_CACHE=${LLAMA_CACHE:-$HOME/llama-rpc/cache}
exec "$release_dir/bin/ggml-rpc-server" -H "$bind_ip" -p "${MYLLAMA_RPC_PORT:-50236}" -d CUDA0 -c "$@"
