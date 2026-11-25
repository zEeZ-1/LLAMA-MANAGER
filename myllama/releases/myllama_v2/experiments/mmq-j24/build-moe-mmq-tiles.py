#!/usr/bin/env python3
"""Build one patched MMQ dispatcher against the release's existing CUDA kernels."""

from pathlib import Path
import argparse
import subprocess
import tempfile


HERE = Path(__file__).resolve().parent
LLAMA = Path("/home/nvidia/llama.cpp")
GGML = LLAMA / "ggml"
CUDA_SRC = GGML / "src/ggml-cuda"
RELEASE = Path("/home/nvidia/llama-deploy/releases/myllama_v1/bin")
NVCC = Path("/usr/local/cuda-13.2/bin/nvcc")

MARKER = '''        J_best = ggml_cuda_mmq_get_J_best(src0->type, ncols_opt, fallback, cc, smpbo, prec_src1, &nthreads_best);
'''

TILE_OVERRIDE = '''
        // Reuse stock kernels. Set J before the route permutation and activation allocation.
        const char * knob = nullptr;
        if (ids && !fusion && cc == GGML_CUDA_CC_ORIN && ne02 == 256 && ids->ne[0] == 8 && ne12 >= 64) {
            if (src0->type == GGML_TYPE_IQ3_S && ne00 == 2048 && ne01 == 512 && ne11 == 1) {
                knob = "MOE_MMQ_IQ3_GATE_J";
            } else if (ne00 == 512 && ne01 == 2048 && ne11 == 8) {
                if (src0->type == GGML_TYPE_IQ3_S) {
                    knob = "MOE_MMQ_IQ3_DOWN_J";
                } else if (src0->type == GGML_TYPE_Q5_K) {
                    knob = "MOE_MMQ_Q5_DOWN_J";
                }
            }
        }
        if (knob) {
            const char * value = std::getenv(knob);
            if (value) {
                char * end = nullptr;
                const long width = std::strtol(value, &end, 10);
                if (*end || (width != 8 && width != 16 && width != 24 && width != 32)) {
                    GGML_ABORT("%s must be one of 8, 16, 24 or 32", knob);
                }
                const auto config = ggml_cuda_mmq_get_config(src0->type, int(width), fallback, cc, prec_src1);
                if (config.type == GGML_TYPE_COUNT || mmq_get_nbytes_shared(config, cc) > smpbo) {
                    GGML_ABORT("%s=%ld does not fit the stock kernel configuration", knob, width);
                }
                if (std::getenv("MOE_MMQ_J_TRACE")) {
                    std::fprintf(stderr, "%s=%ld replaces J=%d for %lld prompt tokens\\n",
                        knob, width, J_best, static_cast<long long>(ne12));
                }
                J_best = int(width);
                nthreads_best = config.nthreads;
            }
        }
'''

CAPTURE_MARKER = '''        const int sis1 = nb12 / nb11;
'''

CAPTURE_CALL = '''
        if (!fusion && cc == GGML_CUDA_CC_ORIN && src0->type == GGML_TYPE_IQ3_S &&
                ne00 == 2048 && ne01 == 512 && ne02 == 256 && ne11 == 1 && ne12 == 512) {
            capture_moe_gate_routes(src0, ids, stream);
        }
'''

CAPTURE_HELPER = '''
#include <array>
#include <mutex>
#include <string>
#include <string_view>

static void capture_moe_gate_routes(const ggml_tensor * weight, const ggml_tensor * ids, cudaStream_t stream) {
    const char * prefix = std::getenv("MOE_MMQ_ROUTE_CAPTURE");
    if (!prefix || !*prefix) {
        return;
    }

    constexpr std::string_view suffix = ".ffn_gate_exps.weight";
    const std::string_view name = ggml_get_name(weight);
    const char * requested = std::getenv("MOE_MMQ_ROUTE_WEIGHT");
    if (name.size() < suffix.size() || name.substr(name.size() - suffix.size()) != suffix ||
            (requested && name != requested)) {
        return;
    }
    static std::mutex capture_mutex;
    static std::string first_weight;
    static int n_captured = 0;
    const std::lock_guard<std::mutex> lock(capture_mutex);
    if (n_captured >= 3 || (!first_weight.empty() && first_weight != name)) {
        return;
    }
    if (!std::getenv("GGML_CUDA_DISABLE_GRAPHS")) {
        GGML_ABORT("set GGML_CUDA_DISABLE_GRAPHS=1 when capturing MoE routes");
    }

    constexpr size_t picks = 8;
    constexpr size_t tokens = 512;
    constexpr size_t row_bytes = picks * sizeof(int32_t);
    if (ids->ne[0] != picks || ids->ne[1] != tokens || ids->ne[2] != 1 || ids->ne[3] != 1 ||
            ids->nb[0] != sizeof(int32_t) || ids->nb[1] < row_bytes) {
        GGML_ABORT("unexpected MoE routing ID layout");
    }
    cudaStreamCaptureStatus status;
    CUDA_CHECK(cudaStreamIsCapturing(stream, &status));
    GGML_ASSERT(status == cudaStreamCaptureStatusNone);
    std::array<int32_t, tokens*picks> rows;
    CUDA_CHECK(cudaMemcpy2DAsync(rows.data(), row_bytes, ids->data, ids->nb[1], row_bytes, tokens,
        cudaMemcpyDeviceToHost, stream));
    CUDA_CHECK(cudaStreamSynchronize(stream));

    const std::string path = std::string(prefix) + "." + std::to_string(n_captured + 1) + ".routes";
    FILE * capture = std::fopen(path.c_str(), "wx");
    if (!capture) {
        GGML_ABORT("cannot create MoE routing file %s", path.c_str());
    }
    std::fprintf(capture, "%zu %zu %s\\n", tokens, picks, ggml_get_name(weight));
    for (size_t token = 0; token < tokens; ++token) {
        for (size_t slot = 0; slot < picks; ++slot) {
            std::fprintf(capture, "%d%c", rows[token*picks + slot], slot + 1 == picks ? '\\n' : ' ');
        }
    }
    if (std::fclose(capture) != 0) {
        GGML_ABORT("cannot write MoE routing file %s", path.c_str());
    }
    first_weight = name;
    ++n_captured;
    std::fprintf(stderr, "MoE route capture saved %s\\n", path.c_str());
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true", help="build a separate route capture overlay")
    options = parser.parse_args()
    source = (CUDA_SRC / "mmq.cu").read_text()
    if source.count(MARKER) != 1:
        raise RuntimeError("MMQ tile selection changed")
    source = "#include <cstdio>\n#include <cstdlib>\n" + source.replace(MARKER, MARKER + TILE_OVERRIDE)
    library_name = "moe-mmq-tiles.so"
    if options.capture:
        helper_marker = "#include <cstdint>\n"
        if source.count(helper_marker) != 1 or source.count(CAPTURE_MARKER) != 1:
            raise RuntimeError("MMQ routing metadata layout changed")
        source = source.replace(helper_marker, helper_marker + CAPTURE_HELPER)
        source = source.replace(CAPTURE_MARKER, CAPTURE_MARKER + CAPTURE_CALL)
        library_name = "moe-mmq-route-capture.so"
    with tempfile.TemporaryDirectory(prefix="mmq-tiles-", dir=HERE) as temp:
        generated = Path(temp) / "mmq-moe-tiles.cu"
        generated.write_text(source)
        library = Path(temp) / library_name
        subprocess.run([
            "nice", "-n", "10", str(NVCC), "--cudart=shared", "-O3", "-DNDEBUG",
            "-std=c++17", "-arch=sm_87", "-Xcompiler=-fPIC", "-use_fast_math",
            "-extended-lambda", "-compress-mode=size", "-DGGML_BACKEND_BUILD",
            "-DGGML_BACKEND_SHARED", "-DGGML_SHARED", "-DGGML_CUDA_USE_GRAPHS",
            "-DGGML_SCHED_MAX_COPIES=4", f"-I{GGML / 'include'}", f"-I{GGML / 'src'}", f"-I{CUDA_SRC}",
            "--shared", str(generated), f"-L{RELEASE}", "-lggml-cuda", "-lggml-base",
            f"-Xlinker=-rpath,{RELEASE}", "-Xlinker=-z,defs", "-o", str(library),
        ], check=True)
        library.replace(HERE / library.name)


if __name__ == "__main__":
    main()
