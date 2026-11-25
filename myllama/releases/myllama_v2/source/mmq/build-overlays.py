from pathlib import Path
import subprocess
import re

here = Path.cwd()
src = Path('/home/nvidia/llama.cpp/ggml')
cuda = src / 'src/ggml-cuda'
release = Path('/home/nvidia/llama-deploy/releases/myllama_v1/bin')
variants = ('control', 'layout64', 'q6-packed')
for variant in variants:
    target = here / variant
    target.mkdir(exist_ok=True)
    for filename in ('mmq.cuh', 'mmq-config-ampere.cuh', 'mmq-load-tiles.cuh', 'mmq-vec-dot.cuh'):
        payload = (cuda / filename).read_text()
        if variant == 'layout64' and filename == 'mmq-config-ampere.cuh':
            payload, count = re.subn(r'(CASE\(GGML_TYPE_Q[46]_K, )256, 1, 128, 128,(.*true, false\);)', r'\g<1>128, 2,  64, 128,\2', payload)
            assert count == 2, count
        if variant == 'q6-packed' and filename == 'mmq-vec-dot.cuh':
            start = payload.index('void ggml_cuda_mmq_vec_dot_q6_K_q8_1_mma(')
            prior = payload[:start]
            suffix = payload[start:]
            end = suffix.index('// Used for', 0) if '// Used for' in suffix else len(suffix)
            assert 'int    scA[ntx][tile_C::ne/2][8];' in suffix[:end]
            suffix = suffix.replace('int    scA[ntx][tile_C::ne/2][8];', 'int    scA[ntx][tile_C::ne/2][2];', 1)
            old = '''                const int      sc_packed = x_sc[i*sram_stride + k0/16];
                const int8_t * sc        = (const int8_t *) &sc_packed;

#pragma unroll
                for (int ksc = 0; ksc < sizeof(int); ++ksc) {
                    scA[n][l][k01/4 + ksc] = sc[ksc];
                }'''
            new = '                scA[n][l][k01/16] = x_sc[i*sram_stride + k0/16];'
            assert old in suffix
            suffix = suffix.replace(old, new, 1)
            old = '                    tmp[n][l] += (C[0].x[l]*scA[n][l/2][k01/4 + 0] + C[1].x[l]*scA[n][l/2][k01/4 + 1])*dB[l%2];'
            new = '''                    const unsigned packed = scA[n][l/2][k01/16];
                    const int scale0 = int8_t(packed >> ((k01 % 16)*2));
                    const int scale1 = int8_t(packed >> ((k01 % 16)*2 + 8));
                    tmp[n][l] += (C[0].x[l]*scale0 + C[1].x[l]*scale1)*dB[l%2];'''
            assert old in suffix
            payload = prior + suffix.replace(old, new, 1)
        (target / filename).write_text(payload)
    cmd = ['/usr/local/cuda-13.2/bin/nvcc', '--shared', '--cudart=shared', '-O3', '-DNDEBUG',
           '-std=c++17', '-arch=sm_87', '-Xcompiler=-fPIC', '-use_fast_math', '-extended-lambda',
           '-DGGML_BACKEND_BUILD', '-DGGML_BACKEND_SHARED', '-DGGML_SHARED', '-DGGML_CUDA_USE_GRAPHS',
           '-DGGML_SCHED_MAX_COPIES=4', '-I'+str(target), '-I'+str(src/'include'),
           '-I'+str(src/'src'), '-I'+str(cuda), str(here/'route.cu'), '-L'+str(release),
           '-lggml-cuda', '-lggml-base', '-ldl', '-Xlinker=-rpath,'+str(release), '-o', str(target/'mmq.so')]
    print('building', variant, flush=True)
    with (target/'build.log').open('w') as log:
        subprocess.run(cmd, stdout=log, stderr=log, check=True)
