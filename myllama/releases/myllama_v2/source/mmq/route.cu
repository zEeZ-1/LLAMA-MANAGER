#include "mmq.cuh"
#include <dlfcn.h>

#define Q4_SYMBOL "_Z14mul_mat_q_caseIL9ggml_type12EL9ggml_prec30EEvR25ggml_backend_cuda_contextRK8mmq_argsP11CUstream_st"
#define Q6_SYMBOL "_Z14mul_mat_q_caseIL9ggml_type14EL9ggml_prec30EEvR25ggml_backend_cuda_contextRK8mmq_argsP11CUstream_st"

extern "C" void route_q4(ggml_backend_cuda_context &, const mmq_args &, cudaStream_t) asm(Q4_SYMBOL);
extern "C" void route_q6(ggml_backend_cuda_context &, const mmq_args &, cudaStream_t) asm(Q6_SYMBOL);

template <ggml_type type>
static void dispatch(ggml_backend_cuda_context & ctx, const mmq_args & args, cudaStream_t stream, const char * name) {
    if (args.J_best == 128 && args.nrows_x % 128 == 0 && !args.fusion.gate && !args.ids_dst) {
        static const bool announce = [] {
            std::fprintf(stderr, "SM87 experiment active for ggml type %d\n", int(type));
            return true;
        }();
        GGML_UNUSED(announce);
        launch_mul_mat_q<type, 128, false, GGML_PREC_Q8, false>(ctx, args, stream);
        return;
    }
    using function = void (*)(ggml_backend_cuda_context &, const mmq_args &, cudaStream_t);
    static auto original = reinterpret_cast<function>(dlsym(RTLD_NEXT, name));
    GGML_ASSERT(original);
    original(ctx, args, stream);
}

extern "C" void route_q4(ggml_backend_cuda_context & ctx, const mmq_args & args, cudaStream_t stream) {
    dispatch<GGML_TYPE_Q4_K>(ctx, args, stream, Q4_SYMBOL);
}

extern "C" void route_q6(ggml_backend_cuda_context & ctx, const mmq_args & args, cudaStream_t stream) {
    dispatch<GGML_TYPE_Q6_K>(ctx, args, stream, Q6_SYMBOL);
}
