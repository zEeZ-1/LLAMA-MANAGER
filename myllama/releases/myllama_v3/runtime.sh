# shellcheck shell=bash
# Sourced by the two release entry points. Libraries in this package take priority.
release_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
kernel_dir="$release_dir/kernels"
export LD_LIBRARY_PATH="$release_dir/bin:${MYLLAMA_CUDA_LIB_DIR:-/usr/local/cuda-13.2/lib64}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export LD_PRELOAD="$kernel_dir/moe-mmq-tiles.so:$kernel_dir/overlay-hybrid.so:$kernel_dir/l2-hint.so:$kernel_dir/mmq-fused.so"
export GGML_RPC_BUFFER_MAX_MIB=${GGML_RPC_BUFFER_MAX_MIB:-512}
