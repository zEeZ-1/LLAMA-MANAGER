#!/usr/bin/env bash
set -euo pipefail
trial_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
python3 "$trial_dir/build-overlay.py"
python3 - "$trial_dir" <<'PY'
import json,subprocess,sys
from pathlib import Path
p=Path(sys.argv[1])
(p/'overlay-row2.cu').write_text((p/'overlay.cu').read_text().replace('mmvq_trial','mmvq_trial2'))
cmd=json.loads((p/'build-command.json').read_text())
cmd=[str(p/'overlay-row2.cu') if x==str(p/'overlay.cu') else str(p/'overlay-row2.so') if x==str(p/'overlay-row4.so') else x for x in cmd]
cmd.insert(1,'-DMMVQ_TRIAL_ROWS=2')
subprocess.run(cmd,check=True)
PY
/usr/local/cuda-13.2/bin/nvcc --cudart=shared -O3 -std=c++17 -arch=sm_87 -Xcompiler=-fPIC -DGGML_CUDA_USE_GRAPHS -DGGML_SCHED_MAX_COPIES=4 -I/home/nvidia/llama.cpp/ggml/include -I/home/nvidia/llama.cpp/ggml/src -I/home/nvidia/llama.cpp/ggml/src/ggml-cuda --shared "$trial_dir/hybrid.cu" -L"$trial_dir" -l:overlay-row2.so -l:overlay-row4.so -L/home/nvidia/llama.cpp/build/bin -lggml-cuda -lggml-base -ldl -Xlinker=-rpath,'$ORIGIN:/home/nvidia/llama.cpp/build/bin' -o "$trial_dir/overlay-hybrid.so"
g++ -std=c++17 -O2 "$trial_dir/bench.cpp" -I/home/nvidia/llama.cpp/ggml/include -L/home/nvidia/llama.cpp/build/bin -Wl,-rpath,/home/nvidia/llama.cpp/build/bin -lggml -lggml-base -o "$trial_dir/bench"
