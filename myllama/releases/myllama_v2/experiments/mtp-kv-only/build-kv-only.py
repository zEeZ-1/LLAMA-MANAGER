#!/usr/bin/env python3
"""Build a preloadable Qwen3.5 MoE MTP cache-only prefill experiment."""

import argparse
import json
import shlex
import subprocess
from pathlib import Path


CONSTRUCTOR = "llama_model_qwen35moe::graph_mtp::graph_mtp(const llm_graph_params & params)"
ORIGINAL = """    Qcur = build_rope(Qcur, inp_pos, il);
    Kcur = build_rope(Kcur, inp_pos, il);
"""
OUTPUT_IDS = "    ggml_tensor * inp_out_ids = build_inp_out_ids();"
CACHE_ONLY_OUTPUT_IDS = """    // The single MTP layer only needs projected K/V for an input whose
    // draft output and draft hidden row are both masked out.
    const bool cache_only = n_outputs == 0 && cparams.embeddings_nextn_masked && cparams.causal_attn && !cparams.training;
    ggml_tensor * inp_out_ids = cache_only ? nullptr : build_inp_out_ids();"""
REPLACEMENT = """    if (!cache_only) {
        Qcur = build_rope(Qcur, inp_pos, il);
    }
    Kcur = build_rope(Kcur, inp_pos, il);

    if (cache_only) {
        const auto * mctx_cur = inp_attn->mctx;
        if (inp_attn->self_k_rot[il]) {
            Kcur = llama_mul_mat_hadamard(ctx0, Kcur, inp_attn->self_k_rot[il]);
        }
        if (inp_attn->self_v_rot[il]) {
            Vcur = llama_mul_mat_hadamard(ctx0, Vcur, inp_attn->self_v_rot[il]);
        }
        if (ggml_tensor * scale = mctx_cur->get_k_scale(il)) {
            Kcur = ggml_div(ctx0, Kcur, scale);
        }
        if (ggml_tensor * scale = mctx_cur->get_v_scale(il)) {
            Vcur = ggml_div(ctx0, Vcur, scale);
        }

        // Match the order used by build_attn() for the projection/cache nodes.
        ggml_build_forward_expand(gf, Vcur);
        ggml_build_forward_expand(gf, Kcur);
        ggml_build_forward_expand(gf, mctx_cur->cpy_k(ctx0, Kcur, inp_attn->get_k_idxs(), il));
        ggml_build_forward_expand(gf, mctx_cur->cpy_v(ctx0, Vcur, inp_attn->get_v_idxs(), il));
        static const bool announced = [] {
            std::fprintf(stderr, "qwen35moe MTP cache-only graph active\\n");
            return true;
        }();
        (void) announced;
        return;
    }

"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/home/nvidia/llama.cpp"))
    parser.add_argument("--release", type=Path, default=Path("/home/nvidia/llama-deploy/releases/myllama_v1"))
    args = parser.parse_args()
    output = Path(__file__).resolve().parent
    model_file = args.source / "src" / "models" / "qwen35moe.cpp"
    model_source = model_file.read_text()
    _, found, constructor = model_source.partition(CONSTRUCTOR)
    if not found or constructor.count(ORIGINAL) != 1 or constructor.count(OUTPUT_IDS) != 1:
        raise SystemExit("The installed Qwen3.5 MoE MTP constructor differs from the reference.")
    constructor = CONSTRUCTOR + constructor.replace(ORIGINAL, REPLACEMENT, 1)
    constructor = constructor.replace(OUTPUT_IDS, CACHE_ONLY_OUTPUT_IDS, 1)
    candidate = output / "qwen35moe-kv-only.cpp"
    candidate.write_text(
        '#include "models/models.h"\n#include "llama-kv-cache.h"\n#include <cstdio>\n\n' + constructor
    )

    commands = json.loads((args.source / "build" / "compile_commands.json").read_text())
    graph_entry = next(entry for entry in commands if Path(entry["file"]).name == "llama-graph.cpp")
    compile_args = shlex.split(graph_entry["command"])
    flags = compile_args[1:compile_args.index("-o")]
    bin_dir = args.release / "bin"
    library = output / "qwen35moe-kv-only.so"
    pending_library = library.with_suffix(".tmp")
    subprocess.run(
        [compile_args[0], *flags, "-shared", "-Wl,-z,defs", "-o", str(pending_library), str(candidate),
         "-L", str(bin_dir), "-Wl,-rpath," + str(bin_dir), "-lllama", "-lggml", "-lggml-base"],
        check=True,
    )
    pending_library.replace(library)
    print(library)


if __name__ == "__main__":
    main()
