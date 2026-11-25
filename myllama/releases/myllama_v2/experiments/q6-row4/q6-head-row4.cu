#include "mmvq.cuh"

#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>

namespace mmvq_trial {
void trial_ggml_cuda_mul_mat_vec_q(ggml_backend_cuda_context &, const ggml_tensor *, const ggml_tensor *,
        const ggml_tensor *, ggml_tensor *, const ggml_cuda_mm_fusion_args_host *);
}

namespace mmvq_trial2 {
void trial_ggml_cuda_mul_mat_vec_q(ggml_backend_cuda_context &, const ggml_tensor *, const ggml_tensor *,
        const ggml_tensor *, ggml_tensor *, const ggml_cuda_mm_fusion_args_host *);
}

void ggml_cuda_mul_mat_vec_q(ggml_backend_cuda_context & ctx, const ggml_tensor * w, const ggml_tensor * x,
        const ggml_tensor * ids, ggml_tensor * dst, const ggml_cuda_mm_fusion_args_host * fusion) {
    const bool eligible = !ids && ggml_cuda_info().devices[ctx.device].cc == GGML_CUDA_CC_ORIN &&
        (w->type == GGML_TYPE_Q4_K || w->type == GGML_TYPE_Q6_K) &&
        x->ne[1] == 1 && x->ne[2] == 1 && x->ne[3] == 1 &&
        w->ne[2] == 1 && w->ne[3] == 1 && w->ne[1] % 2 == 0 &&
        ggml_is_contiguous(w) && ggml_is_contiguous(x) && ggml_is_contiguous(dst);
    if (eligible) {
        const bool head_35b = w->ne[0] == 2048 && w->ne[1] == 248320;
        const bool head_14b = w->ne[0] == 5120 && w->ne[1] == 151936;
        if (!fusion && w->type == GGML_TYPE_Q6_K && (head_14b || head_35b)) {
            if (head_35b && std::getenv("Q6_HEAD_ROW4_TRACE")) {
                static const int logged = std::fprintf(stderr, "q6-head-row4: selected SM87 Q6_K [2048,248320], n=1\n");
                (void) logged;
            }
            mmvq_trial::trial_ggml_cuda_mul_mat_vec_q(ctx, w, x, ids, dst, fusion);
        } else {
            mmvq_trial2::trial_ggml_cuda_mul_mat_vec_q(ctx, w, x, ids, dst, fusion);
        }
        return;
    }

    using matvec_fn = decltype(&ggml_cuda_mul_mat_vec_q);
    static void * native = dlopen("libggml-cuda.so.1", RTLD_LAZY | RTLD_NOLOAD);
    static const auto original = reinterpret_cast<matvec_fn>(dlsym(native,
        "_Z23ggml_cuda_mul_mat_vec_qR25ggml_backend_cuda_contextPK11ggml_tensorS3_S3_PS1_PK29ggml_cuda_mm_fusion_args_host"));
    if (!native || !original) {
        GGML_ABORT("native MMVQ unavailable");
    }
    original(ctx, w, x, ids, dst, fusion);
}
