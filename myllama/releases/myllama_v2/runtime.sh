#!/usr/bin/env bash

myllama_runtime() {
    local role=$1
    local experiment=${MYLLAMA_EXPERIMENT:-none}
    local lib_path="$release_dir/bin:${MYLLAMA_CUDA_LIB_DIR:-/usr/local/cuda-13.2/lib64}"
    local extra="$release_dir/experiments"
    local -a preloads=("$release_dir/bin/overlay-hybrid.so")
    runtime_settings=()

    case "$experiment" in
        none) ;;
        native) preloads=() ;;
        l2-prefetch) preloads+=("$extra/weight-prefetch/l2-hint.so") ;;
        async-ca|async-cg) preloads+=("$extra/weight-prefetch/$experiment.so") ;;
        mmq-layout64) preloads+=("$extra/mmq-layout64/mmq-fused.so") ;;
        mmq-layout64-l2)
            preloads+=("$extra/weight-prefetch/l2-hint.so" "$extra/mmq-layout64/mmq-fused.so")
            ;;
        mmq-j24)
            preloads=("$extra/mmq-j24/moe-mmq-tiles.so" "${preloads[@]}")
            runtime_settings+=(
                "MOE_MMQ_IQ3_GATE_J=${MOE_MMQ_IQ3_GATE_J:-24}"
                "MOE_MMQ_IQ3_DOWN_J=${MOE_MMQ_IQ3_DOWN_J:-24}"
            )
            ;;
        iq3s-fusion) lib_path="$extra/iq3s-fusion/bin:$lib_path" ;;
        mtp-kv-only)
            if [[ $role == server ]]; then
                preloads+=("$extra/mtp-kv-only/qwen35moe-kv-only.so")
            fi
            ;;
        q6-row4) preloads=("$extra/q6-row4/q6-head-row4.so" "${preloads[@]}") ;;
        *) printf 'Unknown MYLLAMA_EXPERIMENT %s\n' "$experiment" >&2; return 2 ;;
    esac

    local preload
    for preload in "${preloads[@]}"; do
        if [[ ! -r $preload ]]; then
            printf 'Missing runtime library %s\n' "$preload" >&2
            return 1
        fi
    done
    local joined
    joined=$(IFS=:; printf '%s' "${preloads[*]}")
    runtime_settings+=(
        "LD_LIBRARY_PATH=$lib_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        "LD_PRELOAD=$joined"
    )
}

myllama_launch() {
    if [[ $dry_run == 1 ]]; then
        printf '%q ' env "${runtime_settings[@]}" "$@"
        printf '\n'
    else
        exec env "${runtime_settings[@]}" "$@"
    fi
}
