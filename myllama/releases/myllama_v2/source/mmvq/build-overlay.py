import hashlib,json,re,subprocess
from pathlib import Path

repo=Path('/home/nvidia/llama.cpp')
out=Path(__file__).resolve().parent
source=repo/'ggml/src/ggml-cuda/mmvq.cu'
text=source.read_text()
body=text[text.index('// only enabled on DGX Spark'):]
marker='static constexpr __host__ __device__ int calc_rows_per_block(ggml_type type, int ncols_dst, int table_id, bool small_k = false, int nwarps = 1) {'
assert body.count(marker)==1
body=body.replace(marker,marker+'''
    if (table_id == MMVQ_PARAMETERS_GENERIC && ncols_dst == 1 && !small_k &&
            (type == GGML_TYPE_Q4_K || type == GGML_TYPE_Q6_K)) {
        return MMVQ_TRIAL_ROWS;
    }
''')
start=body.index('static void mul_mat_vec_q_switch_type(')
end=body.index('\nvoid ggml_cuda_mul_mat_vec_q(',start)
dispatch=body[start:end]
switch=dispatch.index('    switch (type_x) {')
kept=[]
for kind in ['Q4_K','Q6_K']:
    match=re.search(r'        case GGML_TYPE_'+kind+r':.*?            break;',dispatch,re.S)
    assert match
    kept.append(match.group())
body=body[:start]+dispatch[:switch]+'    switch (type_x) {\n'+'\n'.join(kept)+'\n        default: GGML_ABORT("unsupported trial type");\n    }\n}\n'+body[end:]
for name in ['ggml_cuda_should_use_mmvq','get_mmvq_mmid_max_batch','ggml_cuda_mul_mat_vec_q','ggml_cuda_op_mul_mat_vec_q']:
    body=re.sub(r'\b'+name+r'\b','trial_'+name,body)
header='''#include "mmvq.cuh"
#include "quantize.cuh"
#include "unary.cuh"
#include "vecdotq.cuh"
#include <dlfcn.h>
#include <cstdint>
#include <type_traits>
#ifndef MMVQ_TRIAL_ROWS
#define MMVQ_TRIAL_ROWS 4
#endif
namespace mmvq_trial {
'''
wrapper='''
}
void ggml_cuda_mul_mat_vec_q(ggml_backend_cuda_context &ctx,
        const ggml_tensor *w, const ggml_tensor *x, const ggml_tensor *ids,
        ggml_tensor *dst, const ggml_cuda_mm_fusion_args_host *fusion) {
    const bool eligible = !ids && ggml_cuda_info().devices[ctx.device].cc == GGML_CUDA_CC_ORIN &&
        (w->type == GGML_TYPE_Q4_K || w->type == GGML_TYPE_Q6_K) &&
        x->ne[1] == 1 && x->ne[2] == 1 && x->ne[3] == 1 &&
        w->ne[2] == 1 && w->ne[3] == 1 && w->ne[1] % MMVQ_TRIAL_ROWS == 0 &&
        ggml_is_contiguous(w) && ggml_is_contiguous(x) && ggml_is_contiguous(dst);
    if (eligible) {
        mmvq_trial::trial_ggml_cuda_mul_mat_vec_q(ctx,w,x,ids,dst,fusion);
        return;
    }
    using fn = decltype(&ggml_cuda_mul_mat_vec_q);
    static auto original = (fn)dlsym(RTLD_NEXT,
        "_Z23ggml_cuda_mul_mat_vec_qR25ggml_backend_cuda_contextPK11ggml_tensorS3_S3_PS1_PK29ggml_cuda_mm_fusion_args_host");
    if (!original) GGML_ABORT("original MMVQ symbol unavailable");
    original(ctx,w,x,ids,dst,fusion);
}
'''
generated=out/'overlay.cu'
generated.write_text(header+body+wrapper)
metadata={'base_commit':subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip(),
          'base_source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
          'generated_source_sha256':hashlib.sha256(generated.read_bytes()).hexdigest(),
          'strategy':'Only Orin SM87, one column, shared contiguous 2D Q4_K/Q6_K weights, M divisible by rows. Reuse original multirow kernel. Other calls forward to deployed library.',
          'upstream_reference':'https://github.com/ggml-org/llama.cpp/pull/31268',
          'license_source':str(repo/'LICENSE')}
(out/'build-metadata.json').write_text(json.dumps(metadata,indent=2)+'\n')
cmd=['/usr/local/cuda-13.2/bin/nvcc','--cudart=shared','-O3','-DNDEBUG','-std=c++17','-arch=sm_87','-Xcompiler=-fPIC','-use_fast_math','-extended-lambda','-compress-mode=size',
     '-DGGML_BACKEND_BUILD','-DGGML_BACKEND_SHARED','-DGGML_SHARED','-DGGML_CUDA_USE_GRAPHS','-DGGML_SCHED_MAX_COPIES=4',
     '-I'+str(repo/'ggml/include'),'-I'+str(repo/'ggml/src'),'-I'+str(repo/'ggml/src/ggml-cuda'),
     '--shared',str(generated),'-L'+str(repo/'build/bin'),'-lggml-cuda','-lggml-base','-ldl',
     '-Xlinker=-rpath,'+str(repo/'build/bin'),'-o',str(out/'overlay-row4.so')]
(out/'build-command.json').write_text(json.dumps(cmd,indent=2)+'\n')
print(' '.join(cmd),flush=True)
subprocess.run(cmd,check=True)
