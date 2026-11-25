#include "common.cuh"
#include "convert.cuh"
#include "fattn.cuh"
#include "flash.h"
#include <cutlass/half.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <random>
#include <vector>

constexpr int dim = 128;
constexpr int q_heads = 40;
constexpr int kv_heads = 8;
constexpr int page = 256;

template <typename T>
class DeviceBuffer {
public:
    explicit DeviceBuffer(size_t n) { CUDA_CHECK(cudaMalloc(&data, n * sizeof(T))); }
    ~DeviceBuffer() { CUDA_CHECK(cudaFree(data)); }
    DeviceBuffer(const DeviceBuffer &) = delete;
    DeviceBuffer & operator=(const DeviceBuffer &) = delete;
    void upload(const T * src, size_t n) {
        CUDA_CHECK(cudaMemcpy(data, src, n * sizeof(T), cudaMemcpyHostToDevice));
    }
    T * data = nullptr;
};

__global__ void cast_output(const half * src, float * dst, size_t n) {
    const size_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) {
        dst[i] = __half2float(src[i]);
    }
}

int round_up(int n, int unit) { return ((n + unit - 1) / unit) * unit; }

int decode_splits(int len, int sm) {
    const int blocks = round_up(len, 128) / 128;
    const int waves_sm = sm * 2;
    if (kv_heads >= 0.8 * waves_sm) {
        return 1;
    }
    std::vector<double> efficiencies(std::min({128, waves_sm, blocks}) + 1);
    int prev = 0;
    for (size_t s = 1; s < efficiencies.size(); ++s) {
        const int per_split = (blocks + s - 1) / s;
        if (per_split != prev) {
            const double waves = double(kv_heads * s) / waves_sm;
            efficiencies[s] = waves / std::ceil(waves);
        }
        prev = per_split;
    }
    const double best = *std::max_element(efficiencies.begin(), efficiencies.end());
    for (size_t s = 1; s < efficiencies.size(); ++s) {
        if (efficiencies[s] >= best * 0.85) {
            return s;
        }
    }
    return 1;
}

template <typename Run>
double graph_time(cudaStream_t stream, Run launch, int replays) {
    constexpr int graph_ops = 32;
    for (int i = 0; i < 5; ++i) {
        launch();
    }
    CUDA_CHECK(cudaStreamSynchronize(stream));
    cudaGraph_t graph;
    cudaGraphExec_t exec;
    CUDA_CHECK(cudaStreamBeginCapture(stream, cudaStreamCaptureModeRelaxed));
    for (int i = 0; i < graph_ops; ++i) {
        launch();
    }
    CUDA_CHECK(cudaStreamEndCapture(stream, &graph));
    CUDA_CHECK(cudaGraphInstantiate(&exec, graph, 0));
    cudaEvent_t start, end;
    CUDA_CHECK(cudaEventCreate(&start));
    CUDA_CHECK(cudaEventCreate(&end));
    std::array<double, 5> samples;
    for (double & sample : samples) {
        CUDA_CHECK(cudaEventRecord(start, stream));
        for (int rep = 0; rep < replays; ++rep) {
            CUDA_CHECK(cudaGraphLaunch(exec, stream));
        }
        CUDA_CHECK(cudaEventRecord(end, stream));
        CUDA_CHECK(cudaEventSynchronize(end));
        float ms;
        CUDA_CHECK(cudaEventElapsedTime(&ms, start, end));
        sample = ms * 1000.0 / (graph_ops * replays);
    }
    CUDA_CHECK(cudaEventDestroy(start));
    CUDA_CHECK(cudaEventDestroy(end));
    CUDA_CHECK(cudaGraphExecDestroy(exec));
    CUDA_CHECK(cudaGraphDestroy(graph));
    std::sort(samples.begin(), samples.end());
    return samples[2];
}

void measure(ggml_backend_cuda_context & cuda, const int queries, const int keys, const ggml_type type) {
    cudaStream_t stream = cuda.stream();
    const int capacity = round_up(keys, page);
    const size_t q_size = size_t(queries) * q_heads * dim;
    const size_t kv_size = size_t(capacity) * kv_heads * dim;
    const size_t used_size = size_t(keys) * kv_heads * dim;
    ggml_context * ctx = ggml_init({64 * 1024, nullptr, true});
    ggml_tensor * q = ggml_permute(ctx, ggml_new_tensor_3d(ctx, GGML_TYPE_F32, dim, q_heads, queries), 0, 2, 1, 3);
    ggml_tensor * k = ggml_permute(ctx, ggml_new_tensor_3d(ctx, type, dim, kv_heads, capacity), 0, 2, 1, 3);
    ggml_tensor * v = ggml_permute(ctx, ggml_new_tensor_3d(ctx, type, dim, kv_heads, capacity), 0, 2, 1, 3);
    ggml_tensor * mask = ggml_new_tensor_2d(ctx, GGML_TYPE_F16, capacity, queries);
    const float scale = 1.f / std::sqrt(float(dim));
    ggml_tensor * out = ggml_flash_attn_ext(ctx, q, k, v, mask, scale, 0.f, 0.f);
    ggml_flash_attn_ext_set_n_kv_max(out, keys);

    DeviceBuffer<float> q_data(q_size), fa_out(q_size);
    DeviceBuffer<unsigned char> k_data(ggml_nbytes(k)), v_data(ggml_nbytes(v));
    DeviceBuffer<unsigned char> native_out(ggml_nbytes(out) + 4 * kv_size + 256);
    DeviceBuffer<ggml_fp16_t> mask_data(size_t(capacity) * queries);
    q->data = q_data.data;
    k->data = k_data.data;
    v->data = v_data.data;
    mask->data = mask_data.data;
    out->data = native_out.data;
    std::mt19937 gen(44);
    std::normal_distribution<float> normal(0.f, 1.f);
    std::vector<float> q_values(q_size), kv_values(kv_size);
    for (float & x : q_values) { x = normal(gen); }
    q_data.upload(q_values.data(), q_values.size());
    for (auto * buffer : {&k_data, &v_data}) {
        for (float & x : kv_values) { x = normal(gen); }
        const size_t bytes = ggml_nbytes(k);
        std::vector<unsigned char> cpu_kv(bytes);
        if (type == GGML_TYPE_Q8_0) {
            const auto n = ggml_quantize_chunk(type, kv_values.data(), cpu_kv.data(), 0, kv_size / dim, dim, nullptr);
            GGML_ASSERT(n == bytes);
        } else {
            ggml_fp32_to_fp16_row(kv_values.data(), reinterpret_cast<ggml_fp16_t *>(cpu_kv.data()), kv_size);
        }
        buffer->upload(cpu_kv.data(), bytes);
    }
    std::vector<ggml_fp16_t> mask_values(size_t(capacity) * queries);
    for (int t = 0; t < queries; ++t) {
        for (int i = 0; i < capacity; ++i) {
            mask_values[size_t(t) * capacity + i] = ggml_fp32_to_fp16(i <= keys - queries + t ? 0.f : -INFINITY);
        }
    }
    mask_data.upload(mask_values.data(), mask_values.size());

    DeviceBuffer<half> fa_q(q_size), fa_k(kv_size), fa_v(kv_size), fa_o(q_size);
    DeviceBuffer<float> lse(q_heads * queries);
    DeviceBuffer<int> pages(capacity / page), offsets(2), used(1);
    std::vector<int> table(capacity / page);
    for (size_t i = 0; i < table.size(); ++i) { table[i] = i; }
    const int prefix[2] = {0, queries};
    pages.upload(table.data(), table.size());
    offsets.upload(prefix, 2);
    used.upload(&keys, 1);

    const bool packed = queries == 1;
    const int q_group = q_heads / kv_heads;
    flash::Flash_fwd_params p{};
    p.b = 1;
    p.q_ptr = fa_q.data;
    p.k_ptr = type == GGML_TYPE_Q8_0 ? static_cast<void *>(fa_k.data) : k->data;
    p.v_ptr = type == GGML_TYPE_Q8_0 ? static_cast<void *>(fa_v.data) : v->data;
    p.o_ptr = fa_o.data;
    p.q_head_stride = p.o_head_stride = (packed ? q_group : 1) * dim;
    p.q_row_stride = p.o_row_stride = (packed ? 1 : q_heads) * dim;
    p.q_batch_stride = p.o_batch_stride = q_size;
    p.k_head_stride = p.v_head_stride = dim;
    p.k_row_stride = p.v_row_stride = kv_heads * dim;
    p.k_batch_stride = p.v_batch_stride = page * dim * kv_heads;
    p.h_k = kv_heads;
    p.h = packed ? kv_heads : q_heads;
    p.h_h_k_ratio = p.h / p.h_k;
    p.seqlen_q = p.total_q = packed ? q_group : queries;
    p.seqlen_k = keys;
    p.seqlen_q_rounded = round_up(p.seqlen_q, 128);
    p.seqlen_k_rounded = round_up(keys, 128);
    p.d = p.d_rounded = dim;
    p.softmax_lse_ptr = lse.data;
    p.cu_seqlens_q = packed ? nullptr : offsets.data;
    p.seqused_k = used.data;
    p.block_table = pages.data;
    p.block_table_batch_stride = table.size();
    p.page_block_size = page;
    p.scale_softmax = p.scale_softmax_rp_dropout = scale;
    p.scale_softmax_log2 = scale * M_LOG2E;
    p.p_dropout = p.rp_dropout = 1.f;
    p.p_dropout_in_uint8_t = 255;
    p.is_causal = !packed;
    p.is_seqlens_k_cumulative = true;
    p.unpadded_lse = true;
    p.seqlenq_ngroups_swapped = packed;
    p.window_size_left = packed ? -1 : keys;
    p.window_size_right = packed ? -1 : 0;
    p.num_splits = packed ? decode_splits(keys, ggml_cuda_info().devices[cuda.device].nsm) : 0;
    DeviceBuffer<float> lse_accum(size_t(std::max(1, p.num_splits)) * p.h * p.seqlen_q);
    DeviceBuffer<float> out_accum(size_t(std::max(1, p.num_splits)) * q_size);
    if (p.num_splits > 1) {
        p.softmax_lseaccum_ptr = lse_accum.data;
        p.oaccum_ptr = out_accum.data;
    }
    const auto native = [&] { ggml_cuda_flash_attn_ext(cuda, out); };
    const auto prepare = [&] {
        ggml_get_to_fp16_cuda(GGML_TYPE_F32)(q->data, fa_q.data, q_size, stream);
        if (type == GGML_TYPE_Q8_0) {
            ggml_get_to_fp16_cuda(type)(k->data, fa_k.data, used_size, stream);
            ggml_get_to_fp16_cuda(type)(v->data, fa_v.data, used_size, stream);
        }
    };
    const auto launch = [&] {
        if (packed) {
            std::abort();
        } else {
            flash::run_mha_fwd_splitkv_dispatch<cutlass::half_t, dim, true>(p, stream);
        }
    };
    const auto complete = [&] {
        prepare();
        launch();
        cast_output<<<(q_size + 255) / 256, 256, 0, stream>>>(fa_o.data, fa_out.data, q_size);
    };

    native();
    complete();
    CUDA_CHECK(cudaStreamSynchronize(stream));
    std::vector<float> reference(q_size), actual(q_size);
    CUDA_CHECK(cudaMemcpy(reference.data(), out->data, q_size * sizeof(float), cudaMemcpyDeviceToHost));
    CUDA_CHECK(cudaMemcpy(actual.data(), fa_out.data, q_size * sizeof(float), cudaMemcpyDeviceToHost));
    double squared_error = 0, squared_ref = 0, max_error = 0;
    for (size_t i = 0; i < q_size; ++i) {
        GGML_ASSERT(std::isfinite(actual[i]) && std::isfinite(reference[i]));
        const double error = double(actual[i]) - reference[i];
        squared_error += error * error;
        squared_ref += double(reference[i]) * reference[i];
        max_error = std::max(max_error, std::abs(error));
    }
    const double nrmse = std::sqrt(squared_error / squared_ref);
    GGML_ASSERT(nrmse < 0.025);
    const int replays = 5;
    const double ggml_us = graph_time(stream, native, replays);
    const double fa2_kernel_us = graph_time(stream, launch, replays);
    const double fa2_compat_us = graph_time(stream, complete, replays);
    std::printf("{\"q\":%d,\"kv\":%d,\"capacity\":%d,\"type\":\"%s\",\"splits\":%d,\"ggml_us\":%.4f,\"fa2_kernel_us\":%.4f,\"fa2_compat_us\":%.4f,\"nrmse\":%.6f,\"max_abs_error\":%.6f}\n",
        queries, keys, capacity, ggml_type_name(type), p.num_splits, ggml_us, fa2_kernel_us, fa2_compat_us, nrmse, max_error);
    std::fflush(stdout);
    ggml_free(ctx);
}

int main() {
    ggml_backend_cuda_context cuda(0);
    for (auto type : {GGML_TYPE_Q8_0}) {
        for (int keys : {128, 1024}) {
            for (int q : {2, 7, 128}) {
                measure(cuda, q, keys, type);
            }
        }
    }
}
