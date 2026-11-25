#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
repo=/home/nvidia/llama.cpp
native_bin=/home/nvidia/llama-deploy/releases/myllama_v1/bin
/usr/local/cuda-13.2/bin/nvcc -std=c++17 -O3 -arch=sm_87 --cudart=shared \
    -DGGML_CUDA_USE_GRAPHS -DGGML_SCHED_MAX_COPIES=4 \
    -I"$repo/ggml/include" -I"$repo/ggml/src" -I"$repo/ggml/src/ggml-cuda" \
    -I"$PWD/fa-src/csrc/flash_attn/src" -I"$PWD/cutlass/include" \
    -c probe.cu -o probe.o
/usr/local/cuda-13.2/bin/nvcc --cudart=shared -arch=sm_87 probe.o flash_fwd_split*_sm80.o \
    -L"$native_bin" -lggml-cuda -lggml-base -lcuda \
    -Xlinker=-rpath,"$native_bin:/usr/local/cuda-13.2/lib64" -o probe
