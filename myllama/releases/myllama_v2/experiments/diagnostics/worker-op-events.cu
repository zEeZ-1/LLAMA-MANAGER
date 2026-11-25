#include "common.cuh"
#include "ggml-backend-impl.h"
#include "ggml-cuda.h"

#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <string>
#include <unistd.h>
#include <vector>

struct ggml_cuda_mm_fusion_args_host;

namespace {
struct event_span {
    cudaEvent_t first{};
    cudaEvent_t last{};
    const char * group{};
    std::string weight;
    std::string type;
};
thread_local bool capturing;
thread_local event_span graph_span;
thread_local std::vector<event_span> operators;

event_span begin(cudaStream_t stream, const char * group, const ggml_tensor * w = nullptr) {
    event_span span{};
    span.group = group;
    if (w != nullptr) {
        span.weight = w->name;
        span.type = ggml_type_name(w->type);
    }
    CUDA_CHECK(cudaEventCreate(&span.first));
    CUDA_CHECK(cudaEventCreate(&span.last));
    CUDA_CHECK(cudaEventRecord(span.first, stream));
    return span;
}

void finish(cudaStream_t stream, event_span span) {
    CUDA_CHECK(cudaEventRecord(span.last, stream));
    operators.push_back(std::move(span));
}

void write_span(FILE * f, event_span & span) {
    float low = 0;
    float high = 0;
    CUDA_CHECK(cudaEventElapsedTime(&low, graph_span.first, span.first));
    CUDA_CHECK(cudaEventElapsedTime(&high, graph_span.first, span.last));
    std::fprintf(f, "%s\t%s\t%s\t%.6f\t%.6f\n",
                 span.group, span.type.c_str(), span.weight.c_str(), low, high);
}
}

extern "C" enum ggml_status ggml_backend_graph_compute_async(ggml_backend_t backend, ggml_cgraph * graph) {
    static const auto real = reinterpret_cast<decltype(&ggml_backend_graph_compute_async)>(
        dlsym(RTLD_NEXT, "ggml_backend_graph_compute_async"));
    const char * file = std::getenv("KERNEL_TRACE_FILE");
    if (!file || access(file, F_OK) != 0 || !ggml_backend_is_cuda(backend)) {
        return real(backend, graph);
    }
    auto & ctx = *static_cast<ggml_backend_cuda_context *>(backend->context);
    const auto stream = ctx.stream();
    graph_span = begin(stream, "graph");
    capturing = true;
    const auto status = real(backend, graph);
    capturing = false;
    CUDA_CHECK(cudaEventRecord(graph_span.last, stream));
    return status;
}

extern "C" void ggml_backend_tensor_get(const ggml_tensor * tensor, void * data, size_t offset, size_t size) {
    static const auto real = reinterpret_cast<decltype(&ggml_backend_tensor_get)>(
        dlsym(RTLD_NEXT, "ggml_backend_tensor_get"));
    real(tensor, data, offset, size);
    if (!graph_span.first || size < 1024*1024) {
        return;
    }
    const char * file = std::getenv("KERNEL_TRACE_FILE");
    if (auto * f = std::fopen(file, "a")) {
        write_span(f, graph_span);
        for (auto & span : operators) {
            write_span(f, span);
        }
        std::fclose(f);
    }
    for (auto & span : operators) {
        CUDA_CHECK(cudaEventDestroy(span.first));
        CUDA_CHECK(cudaEventDestroy(span.last));
    }
    operators.clear();
    CUDA_CHECK(cudaEventDestroy(graph_span.first));
    CUDA_CHECK(cudaEventDestroy(graph_span.last));
    graph_span = {};
}

void ggml_cuda_mul_mat_q(ggml_backend_cuda_context & ctx, const ggml_tensor * x, const ggml_tensor * y,
                        const ggml_tensor * ids, ggml_tensor * output, const ggml_cuda_mm_fusion_args_host * fusion) {
    static const auto real = reinterpret_cast<decltype(&ggml_cuda_mul_mat_q)>(
        dlsym(RTLD_NEXT, "_Z19ggml_cuda_mul_mat_qR25ggml_backend_cuda_contextPK11ggml_tensorS3_S3_PS1_PK29ggml_cuda_mm_fusion_args_host"));
    event_span span;
    if (capturing) {
        span = begin(ctx.stream(), ids ? "MoE.MMQ" : "dense.MMQ", x);
    }
    real(ctx, x, y, ids, output, fusion);
    if (capturing) {
        finish(ctx.stream(), std::move(span));
    }
}

#define MEASURE_OPERATION(name, symbol, group) \
void name(ggml_backend_cuda_context & ctx, ggml_tensor * dst) { \
    static const auto real = reinterpret_cast<decltype(&name)>(dlsym(RTLD_NEXT, symbol)); \
    event_span span; \
    if (capturing) { span = begin(ctx.stream(), group, dst); } \
    real(ctx, dst); \
    if (capturing) { finish(ctx.stream(), std::move(span)); } \
}
MEASURE_OPERATION(ggml_cuda_flash_attn_ext, "_Z24ggml_cuda_flash_attn_extR25ggml_backend_cuda_contextP11ggml_tensor", "flash_attn")
MEASURE_OPERATION(ggml_cuda_op_gated_delta_net, "_Z28ggml_cuda_op_gated_delta_netR25ggml_backend_cuda_contextP11ggml_tensor", "gdn")
