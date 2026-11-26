#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/runtime.sh"
model_name=Qwen_Qwen3.5-35B-A3B-IQ3_M.gguf
default_model_dir=$HOME/models/Qwen3.5-35B-A3B-IQ3_M
if [[ -f "$release_dir/models/$model_name" ]]; then default_model_dir=$release_dir/models; fi
model_dir=${MYLLAMA_MODEL_DIR:-$default_model_dir}
model=${MYLLAMA_MODEL:-$model_dir/$model_name}
rpc=${MYLLAMA_RPC_SERVERS:-192.168.50.12:50236,192.168.50.11:50236}
first=${rpc%%,*}
if [[ $first == "$rpc" || ${rpc#*,} == *,* ]]; then
    echo 'MYLLAMA_RPC_SERVERS requires two comma-separated endpoints' >&2
    exit 2
fi
mtp=${MYLLAMA_MTP:-0}
default_split=11.5,14,16.5
if [[ $mtp == 1 ]]; then default_split=13.5,15,13.5; fi
opts=(
    -m "$model" --host "${MYLLAMA_HTTP_HOST:-127.0.0.1}" --port "${MYLLAMA_HTTP_PORT:-18236}"
    --rpc "$rpc" --device RPC0,CUDA0,RPC1 --split-mode layer
    --tensor-split "${MYLLAMA_TENSOR_SPLIT:-$default_split}"
    --n-gpu-layers 99 --fit off --ctx-size 5120 --parallel 1
    --batch-size 4096 --ubatch-size 832 --threads 4 --flash-attn on
    --cache-type-k q8_0 --cache-type-v q8_0 --load-mode mmap --no-host --no-repack
    --cache-ram 0 --ctx-checkpoints 0 --no-context-shift --no-mmproj -lv 4
    --rpc-prefill-schedule pipeline --rpc-prefill-window 5
)
case $mtp in
    1) opts+=(
        --spec-type draft-mtp --spec-draft-n-max 2 --spec-draft-n-ubatch 256
        --spec-draft-p-min 0 --spec-draft-type-k q8_0 --spec-draft-type-v q8_0
    ) ;;
    0) opts+=(--spec-type none) ;;
    *) echo 'MYLLAMA_MTP must be 0 or 1' >&2; exit 2 ;;
esac
opts+=(--override-tensor "^token_embd[.]weight$=RPC0[$first]")
if [[ ${1:-} == --dry-run ]]; then
    shift
    printf '%q ' "$release_dir/bin/llama-server" "${opts[@]}" "$@"
    printf '\n'
    exit 0
fi
exec "$release_dir/bin/llama-server" "${opts[@]}" "$@"
