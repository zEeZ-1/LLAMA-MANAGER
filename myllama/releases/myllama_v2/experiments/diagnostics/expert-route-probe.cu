#include "common.cuh"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <unistd.h>
#include <vector>

struct ggml_cuda_mm_fusion_args_host;

void ggml_cuda_mul_mat_q(ggml_backend_cuda_context & ctx, const ggml_tensor * x, const ggml_tensor * y,
                        const ggml_tensor * ids, ggml_tensor * output, const ggml_cuda_mm_fusion_args_host * fusion) {
    static const auto real = reinterpret_cast<decltype(&ggml_cuda_mul_mat_q)>(
        dlsym(RTLD_NEXT, "_Z19ggml_cuda_mul_mat_qR25ggml_backend_cuda_contextPK11ggml_tensorS3_S3_PS1_PK29ggml_cuda_mm_fusion_args_host"));

    const char * path = std::getenv("EXPERT_ROUTE_FILE");
    int layer = -1;
    int suffix_offset = 0;
    if (path && ids && x->ne[2] == 256 && ids->ne[0] == 8 && ids->ne[1] == 1024 &&
        std::sscanf(x->name, "blk.%d%n", &layer, &suffix_offset) == 1 && layer >= 0 && layer < 40 &&
        std::strcmp(x->name + suffix_offset, ".ffn_gate_exps.weight") == 0 && access(path, F_OK) == 0) {
        constexpr size_t picks = 8;
        constexpr size_t tokens = 1024;
        constexpr size_t row_bytes = picks * sizeof(int32_t);
        GGML_ASSERT(ids->nb[0] == sizeof(int32_t));
        GGML_ASSERT(ids->nb[1] >= row_bytes);
        auto stream = ctx.stream();
        std::vector<int32_t> rows(tokens*picks);
        CUDA_CHECK(cudaMemcpy2DAsync(rows.data(), row_bytes, ids->data, ids->nb[1], row_bytes,
                                   tokens, cudaMemcpyDeviceToHost, stream));
        CUDA_CHECK(cudaStreamSynchronize(stream));
        std::vector<uint8_t> packed(rows.size());
        for (size_t i = 0; i < rows.size(); ++i) {
            GGML_ASSERT(rows[i] >= 0 && rows[i] < 256);
            packed[i] = static_cast<uint8_t>(rows[i]);
        }
        FILE * file = std::fopen(path, "ab");
        GGML_ASSERT(file != nullptr);
        const uint32_t packet[] = {static_cast<uint32_t>(layer), tokens, picks};
        GGML_ASSERT(std::fwrite(packet, sizeof(packet), 1, file) == 1);
        GGML_ASSERT(std::fwrite(packed.data(), packed.size(), 1, file) == 1);
        GGML_ASSERT(std::fclose(file) == 0);
    }
    real(ctx, x, y, ids, output, fusion);
}
