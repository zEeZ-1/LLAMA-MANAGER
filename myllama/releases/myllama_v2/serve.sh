#!/usr/bin/env bash
set -euo pipefail
release_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$release_dir/runtime.sh"

if [[ ${1:-} == --help ]]; then
    printf '%s\n' 'Usage: ./serve.sh [35b-prefill|35b-full|14b-dspark] [--dry-run] [--] [llama-server options]'
    printf '%s\n' 'Default profile: 35b-prefill. See README.md for environment settings and experiments.'
    exit 0
fi

profile=${MYLLAMA_PROFILE:-35b-prefill}
if [[ $# -gt 0 && $1 != -* ]]; then
    profile=$1
    shift
fi
dry_run=0
if [[ ${1:-} == --dry-run ]]; then
    dry_run=1
    shift
fi
if [[ ${1:-} == -- ]]; then
    shift
fi
if [[ $# -eq 1 && ( $1 == --help || $1 == --version ) ]]; then
    myllama_runtime server
    myllama_launch "$release_dir/bin/llama-server" "$1"
    exit 0
fi

models=${MYLLAMA_MODEL_DIR:-$release_dir/models}
common=(
    --host "${MYLLAMA_HTTP_HOST:-127.0.0.1}" --port "${MYLLAMA_HTTP_PORT:-18080}"
    --rpc "${MYLLAMA_RPC_SERVERS:-192.168.50.12:50052,192.168.50.11:50052}"
    --split-mode layer --n-gpu-layers 99 --fit off --parallel 1 --threads 4
    --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0
    --load-mode mmap --no-host --no-repack --cache-ram 0 --ctx-checkpoints 0 -lv 4
)
case "$profile" in
    35b-prefill|35b-full)
        devices=RPC0,RPC1,CUDA0
        if [[ $profile == 35b-prefill ]]; then devices=RPC0,CUDA0,RPC1; fi
        options=(
            -m "$models/Qwen_Qwen3.5-35B-A3B-IQ3_M.gguf"
            --device "$devices" --tensor-split 13.5,15,13.5
            --ctx-size 5120 --batch-size 4096 --ubatch-size 1024
            --no-context-shift --no-mmproj
            --spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-n-ubatch 256
            --spec-draft-p-min 0 --spec-draft-type-k q8_0 --spec-draft-type-v q8_0
        )
        ;;
    14b-dspark)
        options=(
            -m "$models/Qwen3-14B-Q4_K_M.gguf" --alias qwen3-14b-q4
            --device RPC0,RPC1,CUDA0 --tensor-split 14,14,13
            --ctx-size 1024 --batch-size 512 --ubatch-size 128
            --jinja --chat-template-kwargs '{"enable_thinking":false}'
            --temp 0.7 --top-p 0.8 --top-k 20 --min-p 0
            --spec-type draft-dspark --model-draft "$models/Qwen3-14B-DSpark-Q4_K_M.gguf"
            --spec-draft-device CUDA0 --spec-draft-ngl 99 --spec-draft-n-max 6 --spec-draft-p-min 0
        )
        ;;
    *) printf 'Unknown profile %s. Run ./serve.sh --help.\n' "$profile" >&2; exit 2 ;;
esac
myllama_runtime server
myllama_launch "$release_dir/bin/llama-server" "${common[@]}" "${options[@]}" "$@"
