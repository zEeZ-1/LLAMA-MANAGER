#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
src="$PWD/fa-src/csrc/flash_attn/src"
nvcc=/usr/local/cuda-13.2/bin/nvcc
for name in flash_fwd_split_hdim128_fp16_causal_sm80 flash_fwd_split_align_hdim128_fp16_causal_sm80 flash_fwd_split_hdim128_fp16_sm80 flash_fwd_split_align_hdim128_fp16_sm80; do
    echo "compiling $name"
    "$nvcc" -std=c++17 -O3 -arch=sm_87 --expt-relaxed-constexpr --expt-extended-lambda --use_fast_math --cudart=shared -Xcompiler=-fPIC \
        -DFLASHATTENTION_DISABLE_DROPOUT -DFLASHATTENTION_DISABLE_BACKWARD -DFLASHATTENTION_DISABLE_PYBIND -DFLASHATTENTION_DISABLE_APPENDKV \
        -DFLASHATTENTION_DISABLE_ALIBI -DFLASHATTENTION_DISABLE_SOFTCAP -DFLASHATTENTION_DISABLE_LOCAL \
        -DCUTLASS_ENABLE_DIRECT_CUDA_DRIVER_CALL=1 -I"$PWD/cutlass/include" \
        -c "$src/$name.cu" -o "$name.o"
done
