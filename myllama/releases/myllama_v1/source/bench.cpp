#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-alloc.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>
#include <unistd.h>

struct bench_case {
    const char * name;
    ggml_type type;
    int64_t k;
    int64_t m;
    int64_t tokens;
    bool fused_graph;
    bool residual_add = false;
};

static double clock_ms() {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

static void fill(std::vector<float> & values, uint32_t & state) {
    for (float & value : values) {
        state = 1664525U * state + 1013904223U;
        value = (float(state >> 8) / float(1U << 23) - 1.0f) * 0.1f;
    }
}

static void upload_weight(ggml_tensor * weight, uint32_t seed) {
    const int64_t k = weight->ne[0];
    const int64_t m = weight->ne[1];
    const size_t row_bytes = ggml_row_size(weight->type, k);
    constexpr int64_t chunk_rows = 64;
    const size_t expected_bytes = ggml_nbytes(weight);
    const std::filesystem::path cache_dir = "/home/nvidia/llama-deploy/profiling/mmvq-strategy/weight-cache";
    std::filesystem::create_directories(cache_dir);
    const std::string cache_name = std::string("lcg-v1-") + ggml_type_name(weight->type) + "-k" + std::to_string(k) + "-m" + std::to_string(m) + "-seed" + std::to_string(seed) + ".bin";
    const auto cache_path = cache_dir / cache_name;
    if (std::filesystem::exists(cache_path) && std::filesystem::file_size(cache_path) == expected_bytes) {
        std::ifstream cache(cache_path, std::ios::binary);
        if (!cache) {
            throw std::runtime_error("cannot open packed weight cache");
        }
        for (int64_t row = 0; row < m; row += chunk_rows) {
            const int64_t count = std::min(chunk_rows, m - row);
            std::vector<uint8_t> packed(row_bytes * size_t(count));
            cache.read(reinterpret_cast<char *>(packed.data()), std::streamsize(packed.size()));
            if (!cache) {
                throw std::runtime_error("short packed weight cache read");
            }
            ggml_backend_tensor_set(weight, packed.data(), size_t(row) * row_bytes, packed.size());
        }
        return;
    }
    const auto temporary_path = cache_dir / (cache_name + ".tmp-" + std::to_string(getpid()));
    std::ofstream cache(temporary_path, std::ios::binary | std::ios::trunc);
    if (!cache) {
        throw std::runtime_error("cannot create packed weight cache");
    }
    for (int64_t row = 0; row < m; row += chunk_rows) {
        const int64_t count = std::min(chunk_rows, m - row);
        std::vector<float> source(size_t(k) * size_t(count));
        std::vector<uint8_t> packed(row_bytes * size_t(count));
        fill(source, seed);
        const size_t bytes = ggml_quantize_chunk(weight->type, source.data(), packed.data(), 0, count, k, nullptr);
        if (bytes != packed.size()) {
            throw std::runtime_error("unexpected quantized weight size");
        }
        cache.write(reinterpret_cast<const char *>(packed.data()), std::streamsize(bytes));
        if (!cache) {
            throw std::runtime_error("failed to write packed weight cache");
        }
        ggml_backend_tensor_set(weight, packed.data(), size_t(row) * row_bytes, bytes);
    }
    cache.close();
    if (!cache || std::filesystem::file_size(temporary_path) != expected_bytes) {
        throw std::runtime_error("incomplete packed weight cache");
    }
    std::filesystem::rename(temporary_path, cache_path);
}

static void compute(ggml_backend_t backend, ggml_cgraph * graph) {
    if (ggml_backend_graph_compute(backend, graph) != GGML_STATUS_SUCCESS) {
        throw std::runtime_error("graph compute failed");
    }
}

static std::string run_case(const bench_case & c, const std::filesystem::path & output_dir, int requested_reps) {
    ggml_backend_t backend = ggml_backend_init_by_name("CUDA0", nullptr);
    if (!backend) {
        throw std::runtime_error("CUDA0 backend initialization failed");
    }
    auto * weight_ctx = ggml_init({1024 * 1024, nullptr, true});
    auto * compute_ctx = ggml_init({1024 * 1024, nullptr, true});
    if (!weight_ctx || !compute_ctx) {
        throw std::runtime_error("ggml context initialization failed");
    }

    auto * weight_up = ggml_new_tensor_2d(weight_ctx, c.type, c.k, c.m);
    ggml_set_name(weight_up, "bench.up.weight");
    auto * weight_gate = c.fused_graph ? ggml_new_tensor_2d(weight_ctx, c.type, c.k, c.m) : nullptr;
    if (weight_gate) {
        ggml_set_name(weight_gate, "bench.gate.weight");
    }
    auto * input = ggml_new_tensor_2d(compute_ctx, GGML_TYPE_F32, c.k, c.tokens);
    ggml_set_name(input, "bench.input");
    ggml_set_input(input);
    auto * up = ggml_mul_mat(compute_ctx, weight_up, input);
    ggml_set_name(up, "bench.up");
    ggml_tensor * output = up;
    if (weight_gate) {
        auto * gate = ggml_mul_mat(compute_ctx, weight_gate, input);
        ggml_set_name(gate, "bench.gate");
        output = ggml_swiglu_split(compute_ctx, gate, up);
        ggml_set_name(output, "bench.swiglu");
    }
    ggml_tensor * residual = nullptr;
    if (c.residual_add) {
        residual = ggml_new_tensor_2d(compute_ctx, GGML_TYPE_F32, c.m, c.tokens);
        ggml_set_name(residual, "bench.residual");
        ggml_set_input(residual);
        output = ggml_add(compute_ctx, output, residual);
        ggml_set_name(output, "bench.residual_sum");
    }
    ggml_set_output(output);
    auto * graph = ggml_new_graph_custom(compute_ctx, 64, false);
    ggml_build_forward_expand(graph, output);

    auto weight_buffer = ggml_backend_alloc_ctx_tensors(weight_ctx, backend);
    auto compute_buffer = ggml_backend_alloc_ctx_tensors(compute_ctx, backend);
    if (!weight_buffer || !compute_buffer) {
        throw std::runtime_error("backend tensor allocation failed");
    }
    ggml_backend_buffer_set_usage(weight_buffer, GGML_BACKEND_BUFFER_USAGE_WEIGHTS);
    ggml_backend_buffer_set_usage(compute_buffer, GGML_BACKEND_BUFFER_USAGE_COMPUTE);
    upload_weight(weight_up, 42);
    if (weight_gate) {
        upload_weight(weight_gate, 1729);
    }
    std::vector<float> input_values(size_t(c.k) * size_t(c.tokens));
    uint32_t input_seed = 123;
    fill(input_values, input_seed);
    ggml_backend_tensor_set(input, input_values.data(), 0, input_values.size() * sizeof(float));
    if (residual) {
        std::vector<float> residual_values(size_t(c.m) * size_t(c.tokens));
        uint32_t residual_seed = 987;
        fill(residual_values, residual_seed);
        ggml_backend_tensor_set(residual, residual_values.data(), 0, residual_values.size() * sizeof(float));
    }

    for (int i = 0; i < 10; ++i) {
        compute(backend, graph);
    }
    const double calibration_start = clock_ms();
    for (int i = 0; i < 4; ++i) {
        compute(backend, graph);
    }
    const double estimate_ms = (clock_ms() - calibration_start) / 4.0;
    const int reps = requested_reps > 0 ? requested_reps : std::clamp(int(50.0 / std::max(estimate_ms, 1e-6)), 4, 256);
    std::vector<double> trials;
    for (int trial = 0; trial < 5; ++trial) {
        const double start = clock_ms();
        for (int i = 0; i < reps; ++i) {
            compute(backend, graph);
        }
        trials.push_back((clock_ms() - start) / reps);
    }
    auto sorted = trials;
    std::sort(sorted.begin(), sorted.end());
    std::vector<float> values(size_t(c.m) * size_t(c.tokens));
    ggml_backend_tensor_get(output, values.data(), 0, values.size() * sizeof(float));
    double sum = 0;
    double sum_squares = 0;
    for (float value : values) {
        if (!std::isfinite(value)) {
            throw std::runtime_error("non-finite output");
        }
        sum += value;
        sum_squares += double(value) * value;
    }
    const std::string output_name = std::string(c.name) + ".bin";
    std::ofstream binary(output_dir / output_name, std::ios::binary);
    binary.write(reinterpret_cast<const char *>(values.data()), std::streamsize(values.size() * sizeof(float)));
    binary.close();
    if (!binary) {
        throw std::runtime_error("failed to save output values");
    }

    char prefix[2048];
    std::snprintf(prefix, sizeof(prefix),
        "{\"case\":\"%s\",\"dtype\":\"%s\",\"k\":%lld,\"m\":%lld,\"tokens\":%lld,\"fused_graph\":%s,\"residual_add\":%s,"
        "\"weight_ne\":[%lld,%lld,1,1],\"input_ne\":[%lld,%lld,1,1],\"output_ne\":[%lld,%lld,1,1],"
        "\"graph_nodes\":%d,\"weight_bytes\":%zu,\"warmup_ops\":10,\"calibration_ops\":4,\"trial_count\":5,\"reps_per_trial\":%d,"
        "\"median_ms\":%.9f,\"min_ms\":%.9f,\"max_ms\":%.9f,\"output_file\":\"%s\",\"output_sum\":%.17g,\"output_sum_squares\":%.17g,\"trials_ms\":[",
        c.name, ggml_type_name(c.type), (long long)c.k, (long long)c.m, (long long)c.tokens, c.fused_graph ? "true" : "false", c.residual_add ? "true" : "false",
        (long long)c.k, (long long)c.m, (long long)c.k, (long long)c.tokens, (long long)c.m, (long long)c.tokens,
        ggml_graph_n_nodes(graph), ggml_nbytes(weight_up) * (c.fused_graph ? 2 : 1), reps,
        sorted[2], sorted.front(), sorted.back(), output_name.c_str(), sum, sum_squares);
    std::string line = prefix;
    for (size_t i = 0; i < trials.size(); ++i) {
        char number[64];
        std::snprintf(number, sizeof(number), "%s%.9f", i ? "," : "", trials[i]);
        line += number;
    }
    line += "]}";
    ggml_backend_buffer_free(compute_buffer);
    ggml_backend_buffer_free(weight_buffer);
    ggml_free(compute_ctx);
    ggml_free(weight_ctx);
    ggml_backend_free(backend);
    return line;
}

int main(int argc, char ** argv) {
    std::filesystem::path output_dir = "operator-results";
    int reps = 0;
    std::string case_filter;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        if (arg == "--output-dir" && i + 1 < argc) {
            output_dir = argv[++i];
        } else if (arg == "--case" && i + 1 < argc) {
            case_filter = argv[++i];
        } else if (arg == "--reps" && i + 1 < argc) {
            char * end = nullptr;
            const long parsed = std::strtol(argv[++i], &end, 10);
            if (!end || *end || parsed < 1 || parsed > 1000000) {
                std::fprintf(stderr, "--reps must be between 1 and 1000000\n");
                return 2;
            }
            reps = int(parsed);
        } else {
            std::fprintf(stderr, "usage: %s [--output-dir PATH] [--reps N] [--case NAME]\n", argv[0]);
            return 2;
        }
    }
    const std::vector<bench_case> cases = {
        {"q4k_qo", GGML_TYPE_Q4_K, 5120, 5120, 1, false},
        {"q4k_k", GGML_TYPE_Q4_K, 5120, 1024, 1, false},
        {"q6k_v", GGML_TYPE_Q6_K, 5120, 1024, 1, false},
        {"q4k_up", GGML_TYPE_Q4_K, 5120, 17408, 1, false},
        {"q6k_down", GGML_TYPE_Q6_K, 17408, 5120, 1, false},
        {"q6k_output", GGML_TYPE_Q6_K, 5120, 151936, 1, false},
        {"q4k_up_gate_swiglu", GGML_TYPE_Q4_K, 5120, 17408, 1, true},
        {"q4k_up_t8", GGML_TYPE_Q4_K, 5120, 17408, 8, false},
        {"q4k_attn_output_residual", GGML_TYPE_Q4_K, 5120, 5120, 1, false, true},
        {"q6k_down_residual", GGML_TYPE_Q6_K, 17408, 5120, 1, false, true},
        {"q4k_down", GGML_TYPE_Q4_K, 17408, 5120, 1, false},
        {"q4k_down_residual", GGML_TYPE_Q4_K, 17408, 5120, 1, false, true},
    };
    if (!case_filter.empty() && std::none_of(cases.begin(), cases.end(), [&](const bench_case & c) { return case_filter == c.name; })) {
        std::fprintf(stderr, "unknown case: %s\n", case_filter.c_str());
        return 2;
    }
    try {
        std::filesystem::create_directories(output_dir);
        std::ofstream jsonl(output_dir / "results.jsonl", std::ios::trunc);
        if (!jsonl) {
            throw std::runtime_error("cannot create results.jsonl");
        }
        ggml_backend_load_all_from_path("/home/nvidia/llama.cpp/build/bin");
        for (const auto & c : cases) {
            if (!case_filter.empty() && case_filter != c.name) {
                continue;
            }
            const std::string line = run_case(c, output_dir, reps);
            jsonl << line << '\n';
            jsonl.flush();
            if (!jsonl) {
                throw std::runtime_error("failed to write results.jsonl");
            }
            std::printf("%s\n", line.c_str());
            std::fflush(stdout);
        }
        ggml_quantize_free();
    } catch (const std::exception & error) {
        std::fprintf(stderr, "benchmark error: %s\n", error.what());
        return 1;
    }
    return 0;
}
