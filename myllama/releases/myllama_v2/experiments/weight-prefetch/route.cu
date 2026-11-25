#include "mmq.cuh"
#include <cstdint>
#include <dlfcn.h>

#define Q4_SYMBOL "_Z14mul_mat_q_caseIL9ggml_type12EL9ggml_prec30EEvR25ggml_backend_cuda_contextRK8mmq_argsP11CUstream_st"
extern "C" void route_q4_prefetch(ggml_backend_cuda_context &, const mmq_args &, cudaStream_t) asm(Q4_SYMBOL);

extern "C" void route_q4_prefetch(ggml_backend_cuda_context & ctx, const mmq_args & args, cudaStream_t stream) {
    const bool aligned = (reinterpret_cast<uintptr_t>(args.x) | reinterpret_cast<uintptr_t>(args.fusion.gate)) % 16 == 0;
    if (args.J_best == 128 && args.nrows_x % 128 == 0 && args.ncols_x % QK_K == 0 &&
        args.fusion.gate && !args.ids_dst && aligned) {
        static const bool announce = [] {
            std::fprintf(stderr, "Q4_K gate/up prefetch mode %d active\n", MMQ_PREFETCH_MODE);
            return true;
        }();
        GGML_UNUSED(announce);
        launch_mul_mat_q<GGML_TYPE_Q4_K, 128, false, GGML_PREC_Q8, true>(ctx, args, stream);
        return;
    }
    using function = void (*)(ggml_backend_cuda_context &, const mmq_args &, cudaStream_t);
    static auto original = reinterpret_cast<function>(dlsym(RTLD_NEXT, Q4_SYMBOL));
    GGML_ASSERT(original);
    original(ctx, args, stream);
}
