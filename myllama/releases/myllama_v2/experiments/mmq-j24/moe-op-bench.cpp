#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml.h"
#include "gguf.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <exception>
#include <fstream>
#include <memory>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

constexpr int experts = 256;
constexpr int picks = 8;

struct bench_case {
    const char * name;
    ggml_type type;
    int k;
    int m;
    int input_slots;
    const char * weight_suffix;
    bool gate_up;
};

constexpr bench_case cases[] = {
    {"iq3-gate", GGML_TYPE_IQ3_S, 2048, 512, 1, ".ffn_gate_exps.weight", false},
    {"iq3-gate-up", GGML_TYPE_IQ3_S, 2048, 512, 1, ".ffn_gate_exps.weight", true},
    {"iq3-down", GGML_TYPE_IQ3_S, 512, 2048, picks, ".ffn_down_exps.weight", false},
    {"q5-down", GGML_TYPE_Q5_K, 512, 2048, picks, ".ffn_down_exps.weight", false},
    {"q6-head", GGML_TYPE_Q6_K, 2048, 248320, 0, "output.weight", false},
};

struct weights {
    int64_t first;
    int64_t second;
};

static weights find_weights(const gguf_context * meta, const bench_case & c) {
    const auto matches = [&](int64_t id) {
        if (id < 0 || gguf_get_tensor_type(meta, id) != c.type) {
            return false;
        }
        const int64_t * dims = gguf_get_tensor_ne(meta, id);
        return dims[0] == c.k && dims[1] == c.m &&
            dims[2] == (c.input_slots ? experts : 1) && dims[3] == 1;
    };
    if (!c.input_slots) {
        const int64_t id = gguf_find_tensor(meta, c.weight_suffix);
        if (matches(id)) {
            return {id, -1};
        }
    } else {
        for (int64_t id = 0; id < gguf_get_n_tensors(meta); ++id) {
            const std::string_view name = gguf_get_tensor_name(meta, id);
            const std::string_view suffix = c.weight_suffix;
            if (!name.ends_with(suffix) || !matches(id)) {
                continue;
            }
            if (!c.gate_up) {
                return {id, -1};
            }
            const std::string up = std::string(name.substr(0, name.size() - suffix.size())) + ".ffn_up_exps.weight";
            const int64_t up_id = gguf_find_tensor(meta, up.c_str());
            if (matches(up_id)) {
                return {id, up_id};
            }
        }
    }
    throw std::runtime_error(std::string("no matching GGUF weights for ") + c.name);
}

static size_t file_offset(const gguf_context * meta, int64_t weight_id) {
    return gguf_get_data_offset(meta) + gguf_get_tensor_offset(meta, weight_id);
}

static void read_bytes(std::ifstream & file, void * dst, size_t offset, size_t bytes) {
    file.seekg(static_cast<std::streamoff>(offset));
    file.read(static_cast<char *>(dst), static_cast<std::streamsize>(bytes));
    if (!file) {
        throw std::runtime_error("failed to read GGUF weight data");
    }
}

static void upload_weight(std::ifstream & file, const gguf_context * meta, int64_t id, ggml_tensor * tensor) {
    const size_t bytes = ggml_nbytes(tensor);
    if (bytes != gguf_get_tensor_size(meta, id)) {
        throw std::runtime_error("GGUF weight size differs from tensor size");
    }
    std::vector<char> chunk(std::min(bytes, size_t(8 * 1024 * 1024)));
    for (size_t offset = 0; offset < bytes; offset += chunk.size()) {
        const size_t count = std::min(chunk.size(), bytes - offset);
        read_bytes(file, chunk.data(), file_offset(meta, id) + offset, count);
        ggml_backend_tensor_set(tensor, chunk.data(), offset, count);
    }
}

static std::vector<int32_t> make_routes(int tokens, bool skew) {
    std::mt19937 random(1719);
    std::uniform_int_distribution<int> choose(0, experts - 1);
    std::uniform_int_distribution<int> choose_hot(0, 31);
    std::uniform_int_distribution<int> choose_cold(32, experts - 1);
    std::bernoulli_distribution use_hot(0.55);
    std::vector<int32_t> ids(tokens * picks);
    for (int token = 0; token < tokens; ++token) {
        const auto first = ids.begin() + token * picks;
        for (auto slot = first; slot != first + picks; ++slot) {
            int id;
            do {
                if (skew) {
                    id = use_hot(random) ? choose_hot(random) : choose_cold(random);
                } else {
                    id = choose(random);
                }
            } while (std::find(first, slot, id) != slot);
            *slot = id;
        }
    }
    return ids;
}

static std::vector<int32_t> load_routes(const std::string & path) {
    std::ifstream file(path);
    int tokens;
    int slots;
    std::string weight;
    if (!(file >> tokens >> slots >> weight) || tokens != 512 || slots != picks) {
        throw std::runtime_error("invalid routing file header");
    }
    std::vector<int32_t> ids(tokens * picks);
    for (int token = 0; token < tokens; ++token) {
        std::array<bool, experts> seen{};
        for (int slot = 0; slot < picks; ++slot) {
            auto & id = ids[token*picks + slot];
            if (!(file >> id) || id < 0 || id >= experts || seen[id]) {
                throw std::runtime_error("invalid expert ID in routing file at token " + std::to_string(token));
            }
            seen[id] = true;
        }
    }
    if (file >> weight) {
        throw std::runtime_error("extra data in routing file");
    }
    return ids;
}

static double reference_dot(std::ifstream & file, const gguf_context * meta, int64_t id,
        const bench_case & c, int expert, int row, const float * input) {
    const size_t row_bytes = ggml_row_size(c.type, c.k);
    std::vector<char> packed(row_bytes);
    std::vector<float> unpacked(c.k);
    read_bytes(file, packed.data(), file_offset(meta, id) + (size_t(expert) * c.m + row) * row_bytes, row_bytes);
    ggml_get_type_traits(c.type)->to_float(packed.data(), unpacked.data(), c.k);
    return std::inner_product(unpacked.begin(), unpacked.end(), input, 0.0);
}

struct validation {
    double nrmse;
    double max_abs;
    int samples;
};

static validation validate(std::ifstream & file, const gguf_context * meta, weights ids, const bench_case & c,
        int tokens, const std::vector<float> & input, const std::vector<int32_t> & routes, ggml_tensor * output) {
    double error_square = 0;
    double value_square = 0;
    double max_abs = 0;
    int samples = 0;
    const int slots = c.input_slots ? picks : 1;
    for (const int token : {0, tokens / 2, tokens - 1}) {
        for (int slot = 0; slot < slots; ++slot) {
            const int expert = c.input_slots ? routes[token * picks + slot] : 0;
            const int input_slot = c.input_slots == picks ? slot : 0;
            const auto * activation = input.data() + (size_t(token) * std::max(1, c.input_slots) + input_slot) * c.k;
            const size_t output_column = size_t(token) * slots + slot;
            for (const int row : {0, c.m / 2, c.m - 1}) {
                const double first = reference_dot(file, meta, ids.first, c, expert, row, activation);
                double expected = first;
                if (c.gate_up) {
                    const double up = reference_dot(file, meta, ids.second, c, expert, row, activation);
                    expected = first / (1.0 + std::exp(-first)) * up;
                }
                float actual;
                ggml_backend_tensor_get(output, &actual, (output_column * c.m + row) * sizeof(float), sizeof(float));
                if (!std::isfinite(actual) || !std::isfinite(expected)) {
                    throw std::runtime_error("non-finite validation sample");
                }
                const double error = std::abs(actual - expected);
                error_square += error * error;
                value_square += expected * expected;
                max_abs = std::max(max_abs, error);
                ++samples;
            }
        }
    }
    return {std::sqrt(error_square / std::max(1e-12, value_square)), max_abs, samples};
}

static double clock_ms() {
    return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

static std::string output_fingerprint(const ggml_tensor * output) {
    std::vector<uint8_t> bytes(ggml_nbytes(output));
    ggml_backend_tensor_get(output, bytes.data(), 0, bytes.size());
    uint64_t hash = 14695981039346656037ULL;
    for (const uint8_t byte : bytes) {
        hash = (hash ^ byte) * 1099511628211ULL;
    }
    char hex[17];
    std::snprintf(hex, sizeof(hex), "%016llx", static_cast<unsigned long long>(hash));
    return hex;
}

static void run_case(ggml_backend_t backend, std::ifstream & file, const gguf_context * meta,
        const bench_case & c, weights ids, int tokens, int reps, bool skew, const std::vector<int32_t> & replay) {
    const auto init_context = [] {
        auto context = std::unique_ptr<ggml_context, decltype(&ggml_free)>(ggml_init({1 << 20, nullptr, true}), ggml_free);
        if (!context) {
            throw std::runtime_error("could not initialize GGML context");
        }
        return context;
    };
    auto weight_ctx = init_context();
    auto compute_ctx = init_context();
    const auto new_weight = [&](int64_t id) {
        auto * tensor = ggml_new_tensor_3d(weight_ctx.get(), c.type, c.k, c.m, c.input_slots ? experts : 1);
        ggml_set_name(tensor, gguf_get_tensor_name(meta, id));
        return tensor;
    };
    auto * first = new_weight(ids.first);
    auto * second = c.gate_up ? new_weight(ids.second) : nullptr;
    auto * activation = c.input_slots ?
        ggml_new_tensor_3d(compute_ctx.get(), GGML_TYPE_F32, c.k, c.input_slots, tokens) :
        ggml_new_tensor_2d(compute_ctx.get(), GGML_TYPE_F32, c.k, tokens);
    ggml_set_input(activation);
    auto * routes_tensor = c.input_slots ?
        ggml_new_tensor_2d(compute_ctx.get(), GGML_TYPE_I32, picks, tokens) : nullptr;
    if (routes_tensor) {
        ggml_set_input(routes_tensor);
    }
    auto * output = routes_tensor ? ggml_mul_mat_id(compute_ctx.get(), first, activation, routes_tensor) :
        ggml_mul_mat(compute_ctx.get(), first, activation);
    if (second) {
        auto * up = ggml_mul_mat_id(compute_ctx.get(), second, activation, routes_tensor);
        output = ggml_swiglu_split(compute_ctx.get(), output, up);
    }
    ggml_set_output(output);
    auto * graph = ggml_new_graph_custom(compute_ctx.get(), 64, false);
    ggml_build_forward_expand(graph, output);
    const auto allocate = [&](ggml_context * ctx, ggml_backend_buffer_usage usage) {
        auto buffer = std::unique_ptr<ggml_backend_buffer, decltype(&ggml_backend_buffer_free)>(
            ggml_backend_alloc_ctx_tensors(ctx, backend), ggml_backend_buffer_free);
        if (!buffer) {
            throw std::runtime_error("could not allocate GGML backend buffer");
        }
        ggml_backend_buffer_set_usage(buffer.get(), usage);
        return buffer;
    };
    auto weight_buffer = allocate(weight_ctx.get(), GGML_BACKEND_BUFFER_USAGE_WEIGHTS);
    auto compute_buffer = allocate(compute_ctx.get(), GGML_BACKEND_BUFFER_USAGE_COMPUTE);
    upload_weight(file, meta, ids.first, first);
    if (second) {
        upload_weight(file, meta, ids.second, second);
    }
    std::mt19937 random(1967);
    std::uniform_real_distribution<float> value(-0.1f, 0.1f);
    std::vector<float> input(ggml_nelements(activation));
    for (auto & v : input) {
        v = value(random);
    }
    ggml_backend_tensor_set(activation, input.data(), 0, ggml_nbytes(activation));
    std::vector<int32_t> routes;
    std::array<int, experts> counts{};
    if (routes_tensor) {
        routes = replay.empty() ? make_routes(tokens, skew) : replay;
        ggml_backend_tensor_set(routes_tensor, routes.data(), 0, ggml_nbytes(routes_tensor));
        for (const auto route : routes) {
            ++counts[route];
        }
    }
    std::sort(counts.begin(), counts.end());
    const auto compute = [&] {
        if (ggml_backend_graph_compute(backend, graph) != GGML_STATUS_SUCCESS) {
            throw std::runtime_error("GGML graph compute failed");
        }
    };
    const double calibration = clock_ms();
    for (int i = 0; i < 5; ++i) {
        compute();
    }
    if (!reps) {
        const double mean_ms = (clock_ms() - calibration) / 5;
        reps = static_cast<int>(std::clamp(100.0 / std::max(1e-6, mean_ms), 1.0, 256.0));
    }
    std::array<double, 5> trial_ms{};
    for (auto & ms : trial_ms) {
        const double start = clock_ms();
        for (int i = 0; i < reps; ++i) {
            compute();
        }
        ms = (clock_ms() - start) / reps;
    }
    std::sort(trial_ms.begin(), trial_ms.end());
    const auto result = validate(file, meta, ids, c, tokens, input, routes, output);
    const bool pass = result.nrmse < 0.07;
    const std::string fingerprint = tokens <= 3 || routes_tensor ? output_fingerprint(output) : "";
    const auto useful_tiles = [&](int width) {
        int tiles = 0;
        for (const int count : counts) {
            tiles += (count + width - 1) / width;
        }
        return tiles;
    };
    std::printf("{\"case\":\"%s\",\"type\":\"%s\",\"k\":%d,\"m\":%d,\"tokens\":%d,"
        "\"expert_slots\":%d,\"route_pattern\":\"%s\",\"routed_experts\":%zu,"
        "\"median_tokens_per_expert\":%d,\"max_tokens_per_expert\":%d,\"useful_tiles_j8_j16_j24_j32\":[%d,%d,%d,%d],"
        "\"weight_bytes\":%zu,\"reps_per_trial\":%d,\"median_ms\":%.6f,\"min_ms\":%.6f,\"max_ms\":%.6f,"
        "\"validation_samples\":%d,\"nrmse\":%.6g,\"max_abs\":%.6g,\"validation_pass\":%s,\"output_fingerprint\":\"%s\"}\n",
        c.name, ggml_type_name(c.type), c.k, c.m, tokens, routes_tensor ? picks : 0,
        !replay.empty() ? "capture" : (skew ? "skew" : "uniform"),
        size_t(counts.end() - std::upper_bound(counts.begin(), counts.end(), 0)), counts[experts / 2], counts.back(),
        useful_tiles(8), useful_tiles(16), useful_tiles(24), useful_tiles(32),
        ggml_nbytes(first) + (second ? ggml_nbytes(second) : 0), reps, trial_ms[2], trial_ms.front(), trial_ms.back(),
        result.samples, result.nrmse, result.max_abs, pass ? "true" : "false", fingerprint.c_str());
    std::fflush(stdout);
    if (!pass) {
        throw std::runtime_error("sampled output exceeds 7% normalized RMS error against GGUF reference");
    }
}

int main(int argc, char ** argv) {
    if (argc < 2 || std::string_view(argv[1]) == "--help") {
        std::puts("Usage: moe-op-bench MODEL.gguf [--list] [--case CASE] [--tokens N] [--reps N] "
            "[--routing uniform|skew | --routes-file PATH] [--backend CUDA0]");
        return argc < 2 ? 1 : 0;
    }
    try {
        bool list = false;
        bool skew = false;
        int tokens = 0;
        int reps = 0;
        const char * device = "CUDA0";
        std::string routes_path;
        bool routing_specified = false;
        std::string_view selected;
        for (int i = 2; i < argc; ++i) {
            const std::string_view option = argv[i];
            if (option == "--list") {
                list = true;
                continue;
            }
            if (++i >= argc) {
                throw std::runtime_error("missing option value");
            }
            if (option == "--case") {
                selected = argv[i];
            } else if (option == "--routing") {
                routing_specified = true;
                const std::string_view pattern = argv[i];
                if (pattern != "uniform" && pattern != "skew") {
                    throw std::runtime_error("--routing must be uniform or skew");
                }
                skew = pattern == "skew";
            } else if (option == "--routes-file") {
                routes_path = argv[i];
            } else if (option == "--tokens") {
                tokens = std::stoi(argv[i]);
                if (tokens < 1 || tokens > 512) {
                    throw std::runtime_error("--tokens must be between 1 and 512");
                }
            } else if (option == "--reps") {
                reps = std::stoi(argv[i]);
                if (reps < 1) {
                    throw std::runtime_error("--reps must be positive");
                }
            } else if (option == "--backend") {
                device = argv[i];
            } else {
                throw std::runtime_error("unknown option: " + std::string(option));
            }
        }
        std::vector<int32_t> replay;
        if (!routes_path.empty()) {
            if (routing_specified || (tokens && tokens != 512)) {
                throw std::runtime_error("--routes-file requires 512 tokens and its own routing pattern");
            }
            replay = load_routes(routes_path);
            tokens = 512;
        }
        auto meta = std::unique_ptr<gguf_context, decltype(&gguf_free)>(
            gguf_init_from_file(argv[1], {true, nullptr}), gguf_free);
        if (!meta) {
            throw std::runtime_error("could not read GGUF metadata");
        }
        std::ifstream file(argv[1], std::ios::binary);
        if (!file) {
            throw std::runtime_error("could not open GGUF weight data");
        }
        std::unique_ptr<ggml_backend, decltype(&ggml_backend_free)> backend(nullptr, ggml_backend_free);
        bool found = false;
        for (const auto & c : cases) {
            if (!selected.empty() && selected != c.name) {
                continue;
            }
            found = true;
            const weights ids = find_weights(meta.get(), c);
            if (list) {
                std::printf("%s %s [%d,%d,%d] %s", c.name, ggml_type_name(c.type), c.k, c.m,
                    c.input_slots ? experts : 1, gguf_get_tensor_name(meta.get(), ids.first));
                if (ids.second >= 0) {
                    std::printf(" %s", gguf_get_tensor_name(meta.get(), ids.second));
                }
                std::puts("");
                continue;
            }
            if (!backend) {
                ggml_backend_load_all();
                backend.reset(ggml_backend_init_by_name(device, nullptr));
                if (!backend) {
                    throw std::runtime_error(std::string("backend initialization failed: ") + device);
                }
            }
            if (tokens) {
                run_case(backend.get(), file, meta.get(), c, ids, tokens, reps, skew, replay);
            } else {
                for (const int batch : {1, 3, 512}) {
                    run_case(backend.get(), file, meta.get(), c, ids, batch, reps, skew, replay);
                }
            }
        }
        if (!found) {
            throw std::runtime_error("unknown case: " + std::string(selected));
        }
    } catch (const std::exception & e) {
        std::fprintf(stderr, "%s\n", e.what());
        return 1;
    }
}
