#!/usr/bin/env python3
"""Build only the CUDA objects needed for the experimental IQ3_S fusion."""
import argparse
import difflib
import json
from pathlib import Path
import shlex
import subprocess


here = Path(__file__).resolve().parent
repo = Path("/home/nvidia/llama.cpp")
stock = repo / "ggml/src/ggml-cuda"
isolated = here / "src/ggml-cuda"
build_cuda = repo / "build/ggml/src/ggml-cuda"
cmake_files = Path("CMakeFiles/ggml-cuda.dir")
units = ("template-instances/mmq-instance-iq3_s.cu", "ggml-cuda.cu")


def edit_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"Expected exactly one occurrence of {old!r}")
    return source.replace(old, new, 1)


def enable_iq3_fusion(source):
    source = edit_once(source, "        type == GGML_TYPE_NVFP4;\n}", "        type == GGML_TYPE_NVFP4 || type == GGML_TYPE_IQ3_S;\n}")
    func = "ggml_cuda_mmq_load_tiles_iq3_s<type, J, fallback>"
    if source.count(func) != 2:
        raise ValueError("Expected DP4A and MMA IQ3_S loaders")
    source = source.replace(func, "ggml_cuda_mmq_load_tiles_iq3_s<type, J, fallback, false>", 1)
    old = f"""{func},
                ggml_cuda_mmq_vec_dot_q8_0_q8_1_mma<type, J, fallback, MMQ_Q8_1_DS_LAYOUT_D4>,
                ggml_cuda_mmq_write_back_mma<type, J, fallback, prec_src1, false>"""
    new = """ggml_cuda_mmq_load_tiles_iq3_s<type, J, fallback, has_fusion>,
                ggml_cuda_mmq_vec_dot_q8_0_q8_1_mma<type, J, fallback, MMQ_Q8_1_DS_LAYOUT_D4>,
                ggml_cuda_mmq_write_back_mma<type, J, fallback, prec_src1, has_fusion>"""
    return edit_once(source, old, new)


def interleave_iq3_rows(source):
    first = source.index("template <ggml_type type, int J, bool fallback> static __device__ __forceinline__ void ggml_cuda_mmq_load_tiles_iq3_s(")
    until = source.index("template <ggml_type type, int J, bool fallback, bool has_fusion> static __device__ __forceinline__ void ggml_cuda_mmq_load_tiles_iq4_xs(", first)
    function = source[first:until]
    function = edit_once(function, "int J, bool fallback>", "int J, bool fallback, bool has_fusion>")
    function = edit_once(function, "    GGML_UNUSED(x_gate);\n\n", "")
    function = edit_once(function,
                         "(const block_iq3_s *) x + kbx0 + i*stride;",
                         "ggml_cuda_mmq_x_row<block_iq3_s, has_fusion>(x, x_gate, kbx0, i, stride);")
    return source[:first] + function + source[until:]


def allow_iq3_ab(source):
    marker = "    // The fused kernel is only compiled for weights whose rows fill whole tiles (no fallback)."
    guard = """    if (x_up->type == GGML_TYPE_IQ3_S) {
        static const bool disabled = ggml_cuda_env_flag("GGML_CUDA_DISABLE_IQ3_S_FUSION");
        if (disabled) {
            return false;
        }
    }

"""
    return edit_once(source, marker, guard + marker)


def prepare():
    isolated.mkdir(parents=True, exist_ok=True)
    edits = {
        "mmq.cuh": enable_iq3_fusion,
        "mmq-load-tiles.cuh": interleave_iq3_rows,
        "ggml-cuda.cu": allow_iq3_ab,
    }
    diff = []
    for name, edit in edits.items():
        original = (stock / name).read_text()
        text = edit(original)
        (isolated / name).write_text(text)
        path = f"ggml/src/ggml-cuda/{name}"
        diff.extend(difflib.unified_diff(original.splitlines(keepends=True), text.splitlines(keepends=True),
                                         fromfile=f"a/{path}", tofile=f"b/{path}"))
    (here / "iq3s-fusion.patch").write_text("".join(diff))
    for path in stock.iterdir():
        if path.name in edits or path.name == "template-instances":
            continue
        destination = isolated / path.name
        if not destination.exists():
            destination.symlink_to(path)
    instance = units[0]
    (isolated / instance).parent.mkdir(exist_ok=True)
    (isolated / instance).write_bytes((stock / instance).read_bytes())


def run(cmd, log_path):
    print(shlex.join(cmd), flush=True)
    with log_path.open("w") as log:
        subprocess.run(cmd, cwd=build_cuda, stdout=log, stderr=subprocess.STDOUT, check=True)


def compile_units():
    table = json.loads((repo / "build/compile_commands.json").read_text())
    by_source = {str(row["file"]): row for row in table}
    replacements = {}
    for unit in units:
        out = here / (Path(unit).name + ".o")
        original = str(stock / unit)
        command = shlex.split(by_source[original]["command"])
        command.insert(1, "-I" + str(isolated.parent))
        command[command.index(original)] = str(isolated / unit)
        output_index = command.index("-o") + 1
        replacements[command[output_index]] = str(out)
        command[output_index] = str(out)
        run(command, here / (out.name + ".log"))
    return replacements


def link(replacements):
    original_list = build_cuda / cmake_files / "objects1.rsp"
    new_list = here / "objects.rsp"
    objects = shlex.split(original_list.read_text())
    if not replacements.keys() <= set(objects):
        raise ValueError("Expected precisely the replaced CUDA objects in the original link")
    new_list.write_text(shlex.join([replacements.get(obj, obj) for obj in objects]) + "\n")

    command = shlex.split((build_cuda / cmake_files / "link.txt").read_text())
    output = here / "libggml-cuda.so.1.0.0"
    command[command.index("-o") + 1] = str(output)
    command[command.index("@" + str(cmake_files / "objects1.rsp"))] = "@" + str(new_list)
    run(command, here / "link.log")
    for name in ("libggml-cuda.so", "libggml-cuda.so.1"):
        path = here / name
        if not path.exists():
            path.symlink_to(output.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    prepare()
    if not args.prepare_only:
        link(compile_units())


if __name__ == "__main__":
    main()
