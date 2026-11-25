#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml-rpc.h"
#include "ggml.h"
#include "gguf.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <memory>
#include <numeric>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
constexpr int hidden = 2048;
constexpr int expansion = 512;
constexpr int experts = 256;
constexpr int tokens = 1024;
constexpr int top_k = 8;

using context_ptr = std::unique_ptr<ggml_context, decltype(&ggml_free)>;
using buffer_ptr = std::unique_ptr<ggml_backend_buffer, decltype(&ggml_backend_buffer_free)>;
using backend_ptr = std::unique_ptr<ggml_backend, decltype(&ggml_backend_free)>;

void require(bool condition, const char * message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

double now_ms() {
    using milliseconds = std::chrono::duration<double, std::milli>;
    return milliseconds(std::chrono::steady_clock::now().time_since_epoch()).count();
}

struct Graph {
    ggml_backend_t backend;
    context_ptr ctx{ggml_init({2 << 20, nullptr, true}), ggml_free};
    buffer_ptr buffer{nullptr, ggml_backend_buffer_free};
    ggml_cgraph * graph = nullptr;

    explicit Graph(ggml_backend_t device) : backend(device) {
        require(ctx != nullptr, "GGML context failed");
    }

    ggml_tensor * tensor(ggml_type type, int64_t ne0, int64_t ne1 = 1, int64_t ne2 = 1) {
        return ggml_new_tensor_3d(ctx.get(), type, ne0, ne1, ne2);
    }

    void allocate(ggml_tensor * output = nullptr) {
        if (output) {
            ggml_set_output(output);
            graph = ggml_new_graph_custom(ctx.get(), 512, false);
            ggml_build_forward_expand(graph, output);
        }
        buffer.reset(ggml_backend_alloc_ctx_tensors(ctx.get(), backend));
        require(buffer != nullptr, "backend buffer allocation failed");
        if (!output) {
            ggml_backend_buffer_set_usage(buffer.get(), GGML_BACKEND_BUFFER_USAGE_WEIGHTS);
        }
    }

    void submit() const {
        require(ggml_backend_graph_compute_async(backend, graph) == GGML_STATUS_SUCCESS,
                "GGML graph submission failed");
    }

    void sync() const {
        ggml_backend_synchronize(backend);
    }

    void run() const {
        submit();
        sync();
    }

    template <typename T>
    void upload(ggml_tensor * tensor, const std::vector<T> & data) const {
        require(ggml_nbytes(tensor) == data.size()*sizeof(T), "tensor upload size mismatch");
        ggml_backend_tensor_set_async(backend, tensor, data.data(), 0, ggml_nbytes(tensor));
    }
};

struct Weights {
    Graph storage;
    std::array<ggml_tensor *, 3> weights{};

    Weights(ggml_backend_t backend, const gguf_context * meta, std::ifstream & file,
            int layer, const std::vector<int> & selected) : storage(backend) {
        constexpr std::array<const char *, 3> suffixes = {"gate", "up", "down"};
        std::array<int64_t, 3> ids{};
        for (size_t i = 0; i < ids.size(); ++i) {
            const std::string name = "blk." + std::to_string(layer) + ".ffn_" + suffixes[i] + "_exps.weight";
            ids[i] = gguf_find_tensor(meta, name.c_str());
            require(ids[i] >= 0, "missing expert tensor");
            const bool down = i == 2;
            const auto * dims = gguf_get_tensor_ne(meta, ids[i]);
            require(dims[0] == (down ? expansion : hidden) && dims[1] == (down ? hidden : expansion) &&
                    dims[2] == experts && dims[3] == 1, "unexpected expert tensor shape");
            weights[i] = storage.tensor(gguf_get_tensor_type(meta, ids[i]), dims[0], dims[1], selected.size());
        }
        storage.allocate();
        for (size_t i = 0; i < ids.size(); ++i) {
            const size_t source = gguf_get_data_offset(meta) + gguf_get_tensor_offset(meta, ids[i]);
            const size_t slab = gguf_get_tensor_size(meta, ids[i]) / experts;
            require(ggml_nbytes(weights[i]) == slab*selected.size(), "expert weight stride mismatch");
            // Run setup outside the A/B, with only whole quantized expert slices.
            std::vector<char> part(slab*selected.size());
            for (size_t j = 0; j < selected.size(); ++j) {
                file.seekg(source + slab*selected[j]);
                file.read(part.data() + slab*j, slab);
                require(bool(file), "GGUF slice read failed");
            }
            ggml_backend_tensor_set(weights[i], part.data(), 0, part.size());
        }
        storage.sync();
    }
};

ggml_tensor * expert_output(ggml_context * ctx, const Weights & w, ggml_tensor * input, ggml_tensor * ids) {
    auto * gate = ggml_mul_mat_id(ctx, w.weights[0], input, ids);
    auto * up = ggml_mul_mat_id(ctx, w.weights[1], input, ids);
    return ggml_mul_mat_id(ctx, w.weights[2], ggml_swiglu_split(ctx, gate, up), ids);
}

ggml_tensor * sum_slots(ggml_context * ctx, ggml_tensor * output, ggml_tensor * coefficients) {
    auto * weighted = ggml_mul(ctx, output, coefficients);
    ggml_tensor * sum = nullptr;
    for (int64_t slot = 0; slot < weighted->ne[1]; ++slot) {
        auto * row = ggml_view_2d(ctx, weighted, hidden, weighted->ne[2], weighted->nb[2], slot*weighted->nb[1]);
        sum = sum ? ggml_add(ctx, sum, row) : row;
    }
    return ggml_cont(ctx, sum);
}

struct PackedRoute {
    struct Batch {
        std::vector<int32_t> sources, experts;
    };
    std::array<Batch, top_k+1> batches;
    std::vector<int32_t> selected_tokens, gather_rows;
    std::vector<float> gather_mask;
    size_t routes = 0;

    PackedRoute(const std::vector<int32_t> & ids, const std::vector<int> & chosen, bool deduplicate_input) {
        std::array<int, experts> mapping{};
        mapping.fill(-1);
        for (size_t i = 0; i < chosen.size(); ++i) {
            mapping[chosen[i]] = int(i);
        }
        for (int token = 0; token < tokens; ++token) {
            std::vector<int32_t> selected;
            for (int j = 0; j < top_k; ++j) {
                const int id = mapping[ids[token*top_k + j]];
                if (id >= 0) {
                    selected.push_back(id);
                }
            }
            if (selected.empty() && deduplicate_input) {
                continue;
            }
            const int source = deduplicate_input ? int(selected_tokens.size()) : token;
            selected_tokens.push_back(token);
            if (!selected.empty()) {
                auto & batch = batches[selected.size()];
                batch.sources.push_back(source);
                batch.experts.insert(batch.experts.end(), selected.begin(), selected.end());
                routes += selected.size();
            }
        }
        require(routes > 0, "no routes for this branch");
        gather_rows.resize(selected_tokens.size());
        gather_mask.resize(selected_tokens.size());
        int32_t row = 0;
        for (const auto & batch : batches) {
            for (int32_t source : batch.sources) {
                gather_rows[source] = row++;
                gather_mask[source] = 1.0f;
            }
        }
    }
};

struct PackedGraph {
    struct BatchInputs {
        ggml_tensor * sources;
        ggml_tensor * ids;
        ggml_tensor * coefficients;
        std::vector<float> scales;
    };
    Graph work;
    const PackedRoute & route;
    ggml_tensor * input;
    ggml_tensor * output;
    ggml_tensor * gather;
    ggml_tensor * mask;
    std::array<BatchInputs, top_k+1> batch_inputs{};

    PackedGraph(ggml_backend_t backend, const Weights & weights, const PackedRoute & routing, ggml_tensor * owner_input)
        : work(backend), route(routing) {
        const int64_t active = route.selected_tokens.size();
        auto * ctx = work.ctx.get();
        input = owner_input ? owner_input : work.tensor(GGML_TYPE_F32, hidden, active);
        ggml_tensor * rows = nullptr;
        for (int slots = 1; slots <= top_k; ++slots) {
            const auto size = route.batches[slots].sources.size();
            if (size == 0) {
                continue;
            }
            auto & batch = batch_inputs[slots];
            batch.sources = work.tensor(GGML_TYPE_I32, size);
            batch.ids = work.tensor(GGML_TYPE_I32, slots, size);
            batch.coefficients = work.tensor(GGML_TYPE_F32, 1, slots, size);
            batch.scales.assign(size*slots, 1.0f/top_k);
            auto * x = ggml_reshape_3d(ctx, ggml_get_rows(ctx, input, batch.sources), hidden, 1, size);
            auto * combined = sum_slots(ctx, expert_output(ctx, weights, x, batch.ids), batch.coefficients);
            rows = rows ? ggml_concat(ctx, rows, combined, 1) : combined;
        }
        gather = work.tensor(GGML_TYPE_I32, active);
        mask = work.tensor(GGML_TYPE_F32, 1, active);
        output = ggml_mul(ctx, ggml_get_rows(ctx, rows, gather), mask);
        work.allocate(output);
        update_route();
        work.sync();
    }

    void update_route() {
        for (int slots = 1; slots <= top_k; ++slots) {
            auto & batch = batch_inputs[slots];
            if (batch.ids) {
                work.upload(batch.sources, route.batches[slots].sources);
                work.upload(batch.ids, route.batches[slots].experts);
                work.upload(batch.coefficients, batch.scales);
            }
        }
        work.upload(gather, route.gather_rows);
        work.upload(mask, route.gather_mask);
    }

    size_t route_bytes() const {
        size_t bytes = ggml_nbytes(gather) + ggml_nbytes(mask);
        for (const auto & batch : batch_inputs) {
            if (batch.ids) {
                bytes += ggml_nbytes(batch.sources) + ggml_nbytes(batch.ids) + ggml_nbytes(batch.coefficients);
            }
        }
        return bytes;
    }
};

std::vector<int32_t> read_plan(std::ifstream & plan, int & layer, std::vector<int> & peer) {
    uint32_t header[2];
    plan.read(reinterpret_cast<char *>(header), sizeof(header));
    require(bool(plan) && header[0] < 40 && header[1] > 0 && header[1] < experts, "invalid benchmark plan");
    layer = header[0];
    std::array<bool, experts> selected{};
    for (uint32_t i = 0; i < header[1]; ++i) {
        char id;
        plan.get(id);
        require(bool(plan) && !selected[uint8_t(id)], "invalid peer expert ID");
        peer.push_back(uint8_t(id));
        selected[uint8_t(id)] = true;
    }
    std::vector<int32_t> ids(tokens*top_k);
    for (auto & id : ids) {
        char value;
        plan.get(value);
        require(bool(plan), "incomplete captured routes");
        id = uint8_t(value);
    }
    return ids;
}

double nrmse(ggml_tensor * reference, ggml_tensor * candidate) {
    std::vector<float> a(hidden*tokens), b(a.size());
    ggml_backend_tensor_get(reference, a.data(), 0, a.size()*sizeof(float));
    ggml_backend_tensor_get(candidate, b.data(), 0, b.size()*sizeof(float));
    double err = 0;
    double scale = 0;
    for (size_t i = 0; i < a.size(); ++i) {
        require(std::isfinite(a[i]) && std::isfinite(b[i]), "non-finite output");
        const double delta = double(a[i])-b[i];
        err += delta*delta;
        scale += double(a[i])*a[i];
    }
    return std::sqrt(err/std::max(1e-20, scale));
}

double median(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    return 0.5*(values[(values.size()-1)/2] + values[values.size()/2]);
}

void run(ggml_backend_t gpu, ggml_backend_t rpc, const char * model, const char * plan_path, int reps) {
    const auto meta = std::unique_ptr<gguf_context, decltype(&gguf_free)>(
        gguf_init_from_file(model, {true, nullptr}), gguf_free);
    require(meta != nullptr, "GGUF metadata load failed");
    std::ifstream file(model, std::ios::binary), plan(plan_path, std::ios::binary);
    std::vector<int> peer_experts;
    int layer;
    const auto ids = read_plan(plan, layer, peer_experts);
    std::vector<int> all(experts), owner_experts;
    std::iota(all.begin(), all.end(), 0);
    for (int id : all) {
        if (std::find(peer_experts.begin(), peer_experts.end(), id) == peer_experts.end()) {
            owner_experts.push_back(id);
        }
    }
    const PackedRoute remote_route(ids, peer_experts, true);
    const PackedRoute local_route(ids, owner_experts, false);
    const PackedRoute all_route(ids, all, false);

    const Weights all_weights(gpu, meta.get(), file, layer, all);
    const Weights local_weights(gpu, meta.get(), file, layer, owner_experts);
    const Weights remote_weights(rpc, meta.get(), file, layer, peer_experts);
    Graph stock(gpu);
    auto * input = stock.tensor(GGML_TYPE_F32, hidden, tokens);
    auto * stock_ids = stock.tensor(GGML_TYPE_I32, top_k, tokens);
    auto * stock_coef = stock.tensor(GGML_TYPE_F32, 1, top_k, tokens);
    auto * stock_output = sum_slots(stock.ctx.get(), expert_output(stock.ctx.get(), all_weights,
        ggml_reshape_3d(stock.ctx.get(), input, hidden, 1, tokens), stock_ids), stock_coef);
    stock.allocate(stock_output);
    std::mt19937 random(1967);
    std::uniform_real_distribution<float> values(-0.1f, 0.1f);
    std::vector<float> x(hidden*tokens);
    for (auto & value : x) {
        value = values(random);
    }
    stock.upload(input, x);
    stock.upload(stock_ids, ids);
    const std::vector<float> coef(tokens*top_k, 1.0f/top_k);
    stock.upload(stock_coef, coef);
    stock.sync();

    const PackedGraph packed(gpu, all_weights, all_route, input);
    const PackedGraph local(gpu, local_weights, local_route, input);
    PackedGraph remote(rpc, remote_weights, remote_route, nullptr);

    const int64_t remote_tokens = remote_route.selected_tokens.size();
    Graph dispatch(gpu);
    auto * token_ids = dispatch.tensor(GGML_TYPE_I32, remote_tokens);
    auto * dispatch_out = ggml_get_rows(dispatch.ctx.get(), input, token_ids);
    dispatch.allocate(dispatch_out);
    dispatch.upload(token_ids, remote_route.selected_tokens);
    dispatch.sync();

    Graph merge(gpu);
    auto * peer_result = merge.tensor(GGML_TYPE_F32, hidden, remote_tokens);
    auto * result_ids = merge.tensor(GGML_TYPE_I32, tokens);
    auto * result_mask = merge.tensor(GGML_TYPE_F32, 1, tokens);
    auto * ctx = merge.ctx.get();
    auto * local_result = ggml_dup_tensor(ctx, local.output);
    require(ggml_backend_tensor_alloc(local.output->buffer, local_result, local.output->data) == GGML_STATUS_SUCCESS,
            "could not alias local output storage");
    auto * merged = ggml_add(ctx, local_result, ggml_mul(ctx, ggml_get_rows(ctx, peer_result, result_ids), result_mask));
    merge.allocate(merged);
    std::vector<int32_t> expand(tokens);
    std::vector<float> mask(tokens);
    for (size_t i = 0; i < remote_route.selected_tokens.size(); ++i) {
        expand[remote_route.selected_tokens[i]] = int32_t(i);
        mask[remote_route.selected_tokens[i]] = 1.0f;
    }
    merge.upload(result_ids, expand);
    merge.upload(result_mask, mask);
    merge.sync();

    std::vector<float> outgoing(size_t(hidden)*remote_tokens), incoming(outgoing.size());
    std::vector<double> stock_times, packed_times, shard_times, dispatch_times, rpc_times, merge_times, local_times;
    const auto shard = [&](bool record) {
        const double begin = now_ms();
        dispatch.run();
        ggml_backend_tensor_get(dispatch_out, outgoing.data(), 0, outgoing.size()*sizeof(float));
        const double packed_at = now_ms();
        local.work.submit();
        remote.work.upload(remote.input, outgoing);
        remote.update_route();
        remote.work.submit();
        ggml_backend_tensor_get_async(rpc, remote.output, incoming.data(), 0, incoming.size()*sizeof(float));
        remote.work.sync();
        const double returned = now_ms();
        local.work.sync();
        merge.upload(peer_result, incoming);
        merge.run();
        const double done = now_ms();
        if (record) {
            dispatch_times.push_back(packed_at-begin);
            rpc_times.push_back(returned-packed_at);
            merge_times.push_back(done-returned);
            shard_times.push_back(done-begin);
        }
    };
    const auto record = [](const Graph & graph, std::vector<double> & times) {
        const double start = now_ms();
        graph.run();
        times.push_back(now_ms()-start);
    };
    for (int i = 0; i < 4; ++i) {
        stock.run();
        packed.work.run();
        shard(false);
    }
    for (int i = 0; i < reps; ++i) {
        if (i%2 == 0) {
            record(stock, stock_times);
            record(packed.work, packed_times);
            shard(true);
        } else {
            shard(true);
            record(packed.work, packed_times);
            record(stock, stock_times);
        }
        record(local.work, local_times);
    }
    const double packed_error = nrmse(stock_output, packed.output);
    const double shard_error = nrmse(stock_output, merged);
    const size_t metadata = remote.route_bytes();
    std::printf(
        "{\"layer\":%d,\"tokens\":%d,\"peer_experts\":%zu,\"peer_tokens\":%lld,\"peer_routes\":%zu,"
        "\"payload_bytes\":%zu,\"reps\":%d,\"stock_ms\":%.4f,\"packed_ms\":%.4f,\"sharded_ms\":%.4f,"
        "\"dispatch_ms\":%.4f,\"rpc_overlap_ms\":%.4f,\"wait_merge_ms\":%.4f,\"owner_partial_ms\":%.4f,"
        "\"packed_nrmse\":%.8g,\"shard_nrmse\":%.8g}\n",
        layer, tokens, peer_experts.size(), (long long) remote_tokens, remote_route.routes,
        (outgoing.size()+incoming.size())*sizeof(float)+metadata, reps, median(stock_times), median(packed_times),
        median(shard_times), median(dispatch_times), median(rpc_times), median(merge_times),
        median(local_times), packed_error, shard_error);
    require(packed_error < 0.01 && shard_error < 0.01, "shard output differs from stock");
}
}

int main(int argc, char ** argv) {
    try {
        require(argc == 5, "usage: expert-rpc-ab MODEL.gguf PLAN.bin PEER:PORT REPS");
        const int reps = std::stoi(argv[4]);
        require(reps > 0, "repetitions must be positive");
        ggml_backend_load_all();
        backend_ptr cuda(ggml_backend_init_by_name("CUDA0", nullptr), ggml_backend_free);
        backend_ptr remote(ggml_backend_rpc_init(argv[3], 0), ggml_backend_free);
        require(cuda && remote, "could not initialize CUDA and RPC");
        run(cuda.get(), remote.get(), argv[1], argv[2], reps);
    } catch (const std::exception & error) {
        std::fprintf(stderr, "%s\n", error.what());
        return 1;
    }
}
