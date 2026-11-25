from pathlib import Path
import subprocess
import sys

directory = Path(__file__).resolve().parent
repo = Path('/home/nvidia/llama.cpp/ggml')
cuda = repo / 'src/ggml-cuda'
release = Path('/home/nvidia/llama-deploy/releases/myllama_v1/bin')
layout = directory.parent / 'layout64'
for name in ('mmq-load-tiles.cuh', 'mmq-vec-dot.cuh', 'mmq-config-ampere.cuh'):
    (directory / name).write_text((layout / name).read_text())
header = (layout / 'mmq.cuh').read_text()

insertion = '''
template <int rows, int threads>
static __device__ __forceinline__ void mmq_prefetch_q4_k(const char *x, const char *gate, int offset, int row_stride, block_q4_K * staging) {
    const int tid = threadIdx.y * 32 + threadIdx.x;
#if MMQ_PREFETCH_MODE == 3
    if (tid < rows) {
        const auto * src = ggml_cuda_mmq_x_row<block_q4_K, true>(x, gate, offset, tid, row_stride);
        const auto * tail = reinterpret_cast<const char *>(src) + 128;
        asm volatile("prefetch.global.L2 [%0];" :: "l"(src) : "memory");
        asm volatile("prefetch.global.L2 [%0];" :: "l"(tail) : "memory");
    }
#else
    static_assert(sizeof(block_q4_K) == 144);
    constexpr int chunks_per_row = sizeof(block_q4_K) / 16;
#pragma unroll
    for (int first = 0; first < rows * chunks_per_row; first += threads) {
        const int chunk = first + tid;
        if (chunk < rows * chunks_per_row) {
            const int row = chunk / chunks_per_row;
            const auto * src_row = ggml_cuda_mmq_x_row<block_q4_K, true>(x, gate, offset, row, row_stride);
            const char * src = reinterpret_cast<const char *>(src_row) + (chunk % chunks_per_row) * 16;
            char * dst = reinterpret_cast<char *>(staging) + chunk * 16;
            const uint32_t address = static_cast<uint32_t>(__cvta_generic_to_shared(dst));
#if MMQ_PREFETCH_MODE == 1
            asm volatile("cp.async.cg.shared.global [%0], [%1], 16;" :: "r"(address), "l"(src) : "memory");
#else
            asm volatile("cp.async.ca.shared.global [%0], [%1], 16;" :: "r"(address), "l"(src) : "memory");
#endif
        }
    }
    asm volatile("cp.async.commit_group;" ::: "memory");
#endif
}

'''
needle = '// has_fusion: x and x_gate are the up and gate weights.'
assert header.count(needle) == 1
header = header.replace(needle, insertion + needle)

needle = '''    for (int kb0 = kb0_start; kb0 < kb0_stop; kb0 += blocks_per_iter) {
        load_tiles(x, x_gate, tile_x, offset_x + kb0, tile_x_i_max, stride_row_x);'''
replacement = '''    constexpr bool prefetch = MMQ_PREFETCH_MODE != 0 && type == GGML_TYPE_Q4_K && has_fusion && J == 128 && I == 64;
    constexpr bool async_stage = prefetch && MMQ_PREFETCH_MODE != 3;
    constexpr int stage_stride = ggml_cuda_mmq_get_sram_stride(GGML_CUDA_MMQ_SRAM_LAYOUT_Q8_1);
    block_q4_K * staging = reinterpret_cast<block_q4_K *>(tile_x + I * stage_stride);
    if constexpr (async_stage) {
        static_assert(blocks_per_iter == 1);
        mmq_prefetch_q4_k<I, nwarps * warp_size>(x, x_gate, offset_x + kb0_start, stride_row_x, staging);
    }

    for (int kb0 = kb0_start; kb0 < kb0_stop; kb0 += blocks_per_iter) {
        if constexpr (async_stage) {
            asm volatile("cp.async.wait_group 0;" ::: "memory");
            __syncthreads();
            ggml_cuda_mmq_load_tiles_q4_K<type, J, false, false>(
                    reinterpret_cast<const char *>(staging), nullptr, tile_x, 0, tile_x_i_max, 1);
        } else {
            load_tiles(x, x_gate, tile_x, offset_x + kb0, tile_x_i_max, stride_row_x);
        }'''
assert header.count(needle) == 1
header = header.replace(needle, replacement)

needle = '''        __syncthreads();

        vec_dot(tile_x, tile_y, sum, 0);'''
replacement = '''        __syncthreads();

        // Unpacking is now complete, so the same packed buffer can stage the next K block.
        if constexpr (prefetch) {
            if (kb0 + blocks_per_iter < kb0_stop) {
                mmq_prefetch_q4_k<I, nwarps * warp_size>(
                        x, x_gate, offset_x + kb0 + blocks_per_iter, stride_row_x, staging);
            }
        }
        vec_dot(tile_x, tile_y, sum, 0);'''
assert header.count(needle) == 1
header = header.replace(needle, replacement)

needle = '    const int nbytes_shared = mmq_get_nbytes_shared(config, cc);'
replacement = '''    const bool async_stage = (MMQ_PREFETCH_MODE == 1 || MMQ_PREFETCH_MODE == 2) &&
                             type == GGML_TYPE_Q4_K && has_fusion && J == 128 && config.I == 64;
    const int nbytes_shared = mmq_get_nbytes_shared(config, cc) +
                              (async_stage ? config.I * sizeof(block_q4_K) : 0);'''
assert header.count(needle) == 1
header = header.replace(needle, replacement)

(directory / 'mmq.cuh').write_text(header)
modes = {'control': 0, 'async-cg': 1, 'async-ca': 2, 'l2-hint': 3}
for mode in (sys.argv[1:] or modes):
    cmd = [ '/usr/local/cuda-13.2/bin/nvcc', '--shared', '--cudart=shared', '-O3', '-DNDEBUG',
        '-std=c++17', '-arch=sm_87', '-Xcompiler=-fPIC', '-use_fast_math', '-extended-lambda',
        '-DGGML_BACKEND_BUILD', '-DGGML_BACKEND_SHARED', '-DGGML_SHARED', '-DGGML_CUDA_USE_GRAPHS',
        '-DGGML_SCHED_MAX_COPIES=4', '-DMMQ_PREFETCH_MODE='+str(modes[mode]),
        '-I'+str(directory), '-I'+str(layout), '-I'+str(repo/'include'), '-I'+str(repo/'src'),
        '-I'+str(cuda), str(directory/'route.cu'), '-L'+str(release), '-lggml-cuda',
        '-lggml-base', '-ldl', '-Xlinker=-rpath,'+str(release), '-o', str(directory/(mode+'.so')) ]
    print('building', mode, flush=True)
    with (directory/(mode+'-build.log')).open('w') as log:
        subprocess.run(cmd, check=True, stdout=log, stderr=log)
