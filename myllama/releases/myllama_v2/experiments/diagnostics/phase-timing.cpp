#include "common.h"
#include "ggml-backend-impl.h"
#include "llama-batch.h"
#include "speculative.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>
#include <fcntl.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

namespace {

thread_local const char * spec_phase = "-";
thread_local const llama_context * active_context = nullptr;
thread_local const llama_context * target_context = nullptr;
thread_local const llama_context * draft_context = nullptr;
thread_local int current_file = -1;
thread_local unsigned long long previous_span_id = 0;
thread_local unsigned long long next_span_id = 0;

unsigned long long monotonic_ns() {
    timespec stamp{};
    clock_gettime(CLOCK_MONOTONIC, &stamp);
    return static_cast<unsigned long long>(stamp.tv_sec) * 1000000000ull + stamp.tv_nsec;
}

int trace_file() {
    if (previous_span_id != 0) {
        return current_file;
    }
    static const char * path = std::getenv("PHASE_TIMING_FILE");
    if (!path || !*path) {
        return -1;
    }

    // The controller creates this file at request start and can rename it at request end.
    const int saved_errno = errno;
    const int file = open(path, O_WRONLY | O_APPEND | O_CLOEXEC);
    errno = saved_errno;
    return file;
}

const char * role(const llama_context * ctx) {
    if (!ctx) {
        return "-";
    }
    if (ctx == draft_context) {
        return "draft";
    }
    if (ctx == target_context) {
        return "target";
    }
    return "unknown";
}

const char * backend_name(ggml_backend_t backend) {
    return backend ? ggml_backend_name(backend) : "-";
}

const char * buffer_name(const ggml_tensor * tensor) {
    if (!tensor) {
        return "-";
    }
    const ggml_backend_buffer_t buffer = tensor->view_src ? tensor->view_src->buffer : tensor->buffer;
    return buffer ? ggml_backend_buffer_name(buffer) : "-";
}

template<class T>
struct thread_scope {
    T & value;
    T previous;

    thread_scope(T & value, T current) : value(value), previous(value) {
        value = current;
    }

    ~thread_scope() {
        value = previous;
    }
};

// Span IDs and their parents are local to a Linux TID. The enclosing stages are
// inclusive. Graph submissions report host time and can enqueue asynchronous GPU work.
class trace_span {
public:
    trace_span(const char * name, const llama_context * ctx = active_context,
               long long tokens = -1, long long position = -1,
               const char * source = "-", const char * destination = "-",
               size_t bytes = 0, const char * detail = "-")
        : file(trace_file()), name(name), ctx(ctx), ctx_role(role(ctx)), phase(spec_phase),
          tokens(tokens), position(position), source(source), destination(destination),
          bytes(bytes), detail(detail) {
        if (file >= 0) {
            parent = previous_span_id;
            id = ++next_span_id;
            previous_span_id = id;
            current_file = file;
            start = monotonic_ns();
        }
    }

    ~trace_span() {
        if (file < 0) {
            return;
        }
        const int saved_errno = errno;
        const unsigned long long end = monotonic_ns();
        previous_span_id = parent;
        static thread_local const long tid = syscall(SYS_gettid);
        char line[1024];
        const int count = std::snprintf(line, sizeof(line),
                "%ld\t%llu\t%llu\t%s\t%s\t%s\t%p\t%lld\t%lld\t%s\t%s\t%zu\t%s\t%llu\t%llu\n",
                tid, id, parent, name, ctx_role, phase, static_cast<const void *>(ctx),
                tokens, position, source, destination, bytes, detail, start, end);
        if (count > 0 && static_cast<size_t>(count) < sizeof(line)) {
            const auto written = write(file, line, count);
            (void) written;
        }
        if (!parent) {
            close(file);
            current_file = -1;
        }
        errno = saved_errno;
    }

private:
    const int file;
    const char * const name;
    const llama_context * const ctx;
    const char * const ctx_role;
    const char * const phase;
    const long long tokens;
    const long long position;
    const char * const source;
    const char * const destination;
    const size_t bytes;
    const char * const detail;
    unsigned long long id = 0;
    unsigned long long parent = 0;
    unsigned long long start = 0;
};

} // namespace

extern "C" int32_t llama_process(llama_context * ctx, llama_process_type type, llama_batch_ext * batch) {
    static auto real = reinterpret_cast<decltype(&llama_process)>(dlsym(RTLD_NEXT, "llama_process"));
    if (spec_phase[0] == '-') {
        target_context = ctx;
    } else {
        draft_context = ctx;
    }
    thread_scope context(active_context, static_cast<const llama_context *>(ctx));
    const long long tokens = batch ? static_cast<long long>(batch->tokens.size()) : -1;
    const long long position = tokens > 0 ? batch->tokens.front().pos[0] : -1;
    trace_span time("llama_process", ctx, tokens, position, "-", "-", 0,
                    type == LLAMA_PROCESS_TYPE_DECODE ? "decode" : "encode");
    return real(ctx, type, batch);
}

extern "C" void llama_synchronize(llama_context * ctx) {
    static auto real = reinterpret_cast<decltype(&llama_synchronize)>(dlsym(RTLD_NEXT, "llama_synchronize"));
    thread_scope context(active_context, static_cast<const llama_context *>(ctx));
    trace_span time("llama_sync", ctx);
    real(ctx);
}

extern "C" bool llama_get_embeddings_nextn_rows(llama_context * ctx, int64_t row, int64_t n_rows, float * out) {
    static auto real = reinterpret_cast<decltype(&llama_get_embeddings_nextn_rows)>(
            dlsym(RTLD_NEXT, "llama_get_embeddings_nextn_rows"));
    thread_scope context(active_context, static_cast<const llama_context *>(ctx));
    trace_span time("mtp_get_h_rows", ctx, n_rows, row);
    return real(ctx, row, n_rows, out);
}

extern "C" bool llama_set_embeddings_nextn_rows(llama_context * ctx, int64_t row, int64_t n_rows, const float * data) {
    static auto real = reinterpret_cast<decltype(&llama_set_embeddings_nextn_rows)>(
            dlsym(RTLD_NEXT, "llama_set_embeddings_nextn_rows"));
    thread_scope context(active_context, static_cast<const llama_context *>(ctx));
    trace_span time("mtp_set_h_rows", ctx, n_rows, row);
    return real(ctx, row, n_rows, data);
}

bool common_speculative_process(common_speculative * spec, const common_batch & batch) {
    static auto real = reinterpret_cast<decltype(&common_speculative_process)>(
            dlsym(RTLD_NEXT, "_Z26common_speculative_processP18common_speculativeRK12common_batch"));
    thread_scope phase(spec_phase, "process");
    const long long position = batch.tokens.empty() ? -1 : batch.tokens.front().pos[0];
    trace_span time("spec_process", active_context, batch.size(), position);
    return real(spec, batch);
}

void common_speculative_draft(common_speculative * spec) {
    static auto real = reinterpret_cast<decltype(&common_speculative_draft)>(
            dlsym(RTLD_NEXT, "_Z24common_speculative_draftP18common_speculative"));
    thread_scope phase(spec_phase, "draft");
    trace_span time("spec_draft", draft_context);
    real(spec);
}

void common_speculative_accept(common_speculative * spec, llama_seq_id seq_id, uint16_t n_accepted) {
    static auto real = reinterpret_cast<decltype(&common_speculative_accept)>(
            dlsym(RTLD_NEXT, "_Z25common_speculative_acceptP18common_speculativeit"));
    thread_scope phase(spec_phase, "accept");
    trace_span time("spec_accept", draft_context, n_accepted);
    real(spec, seq_id, n_accepted);
}

llama_token common_sampler_sample(common_sampler * sampler, llama_context * ctx, int idx, bool grammar_first) {
    static auto real = reinterpret_cast<decltype(&common_sampler_sample)>(
            dlsym(RTLD_NEXT, "_Z21common_sampler_sampleP14common_samplerP13llama_contextib"));
    thread_scope context(active_context, static_cast<const llama_context *>(ctx));
    trace_span time("sample", ctx, -1, idx);
    return real(sampler, ctx, idx, grammar_first);
}

extern "C" enum ggml_status ggml_backend_graph_compute_async(ggml_backend_t backend, ggml_cgraph * graph) {
    static auto real = reinterpret_cast<decltype(&ggml_backend_graph_compute_async)>(
            dlsym(RTLD_NEXT, "ggml_backend_graph_compute_async"));
    trace_span time("graph_submit", active_context, -1, ggml_graph_n_nodes(graph), "-", backend_name(backend));
    return real(backend, graph);
}

extern "C" void ggml_backend_synchronize(ggml_backend_t backend) {
    static auto real = reinterpret_cast<decltype(&ggml_backend_synchronize)>(
            dlsym(RTLD_NEXT, "ggml_backend_synchronize"));
    trace_span time("backend_sync", active_context, -1, -1, backend_name(backend));
    real(backend);
}

extern "C" void ggml_backend_event_synchronize(ggml_backend_event_t event) {
    static auto real = reinterpret_cast<decltype(&ggml_backend_event_synchronize)>(
            dlsym(RTLD_NEXT, "ggml_backend_event_synchronize"));
    trace_span time("event_sync", active_context, -1, -1,
                    event && event->device ? ggml_backend_dev_name(event->device) : "-");
    real(event);
}

extern "C" void ggml_backend_tensor_copy(const ggml_tensor * src, ggml_tensor * dst) {
    static auto real = reinterpret_cast<decltype(&ggml_backend_tensor_copy)>(
            dlsym(RTLD_NEXT, "ggml_backend_tensor_copy"));
    trace_span time("tensor_copy", active_context, -1, -1,
                    buffer_name(src), buffer_name(dst), ggml_nbytes(src), src->name);
    real(src, dst);
}

extern "C" void ggml_backend_tensor_copy_async(ggml_backend_t from, ggml_backend_t to,
                                               const ggml_tensor * src, ggml_tensor * dst) {
    static auto real = reinterpret_cast<decltype(&ggml_backend_tensor_copy_async)>(
            dlsym(RTLD_NEXT, "ggml_backend_tensor_copy_async"));
    trace_span time("tensor_copy_async", active_context, -1, -1,
                    backend_name(from), backend_name(to), ggml_nbytes(src), src->name);
    real(from, to, src, dst);
}

extern "C" void ggml_backend_tensor_get(const ggml_tensor * tensor, void * data, size_t offset, size_t size) {
    static auto real = reinterpret_cast<decltype(&ggml_backend_tensor_get)>(
            dlsym(RTLD_NEXT, "ggml_backend_tensor_get"));
    trace_span time("tensor_get", active_context, -1, -1,
                    buffer_name(tensor), "host", size, tensor->name);
    real(tensor, data, offset, size);
}

extern "C" void ggml_backend_tensor_set(ggml_tensor * tensor, const void * data, size_t offset, size_t size) {
    static auto real = reinterpret_cast<decltype(&ggml_backend_tensor_set)>(
            dlsym(RTLD_NEXT, "ggml_backend_tensor_set"));
    trace_span time("tensor_set", active_context, -1, -1,
                    "host", buffer_name(tensor), size, tensor->name);
    real(tensor, data, offset, size);
}

extern "C" void ggml_backend_tensor_get_async(ggml_backend_t backend, const ggml_tensor * tensor,
                                              void * data, size_t offset, size_t size) {
    static auto real = reinterpret_cast<decltype(&ggml_backend_tensor_get_async)>(
            dlsym(RTLD_NEXT, "ggml_backend_tensor_get_async"));
    trace_span time("tensor_get_async", active_context, -1, -1,
                    backend_name(backend), "host", size, tensor->name);
    real(backend, tensor, data, offset, size);
}

extern "C" void ggml_backend_tensor_set_async(ggml_backend_t backend, ggml_tensor * tensor,
                                              const void * data, size_t offset, size_t size) {
    static auto real = reinterpret_cast<decltype(&ggml_backend_tensor_set_async)>(
            dlsym(RTLD_NEXT, "ggml_backend_tensor_set_async"));
    trace_span time("tensor_set_async", active_context, -1, -1,
                    "host", backend_name(backend), size, tensor->name);
    real(backend, tensor, data, offset, size);
}
