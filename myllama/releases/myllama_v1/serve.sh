#!/usr/bin/env bash
set -euo pipefail
release_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runtime_libs="$release_dir/bin:${MYLLAMA_CUDA_LIB_DIR:-/usr/local/cuda-13.2/lib64}"
export LD_LIBRARY_PATH="$runtime_libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LD_PRELOAD="$release_dir/bin/overlay-hybrid.so"

exec "$release_dir/bin/llama-server" \
    --model "$release_dir/models/Qwen3-14B-Q4_K_M.gguf" \
    --alias qwen3-14b-q4 --host 127.0.0.1 --port "${MYLLAMA_HTTP_PORT:-18080}" \
    --rpc "${MYLLAMA_RPC_SERVERS:-192.168.50.12:50162,192.168.50.11:50162}" \
    --device RPC0,RPC1,CUDA0 --split-mode layer --tensor-split 14,14,13 \
    --n-gpu-layers 99 --fit off --ctx-size 1024 --parallel 1 \
    --threads 4 --batch-size 512 --ubatch-size 128 \
    --flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 \
    --load-mode mmap --no-host --no-repack \
    --cache-ram 0 --ctx-checkpoints 0 --jinja \
    --chat-template-kwargs '{"enable_thinking":false}' \
    --temp 0.7 --top-p 0.8 --top-k 20 --min-p 0 -lv 4 \
    --spec-type draft-dspark --model-draft "$release_dir/models/Qwen3-14B-DSpark-Q4_K_M.gguf" \
    --spec-draft-device CUDA0 --spec-draft-ngl 99 --spec-draft-n-max 6 --spec-draft-p-min 0 \
    "$@"
