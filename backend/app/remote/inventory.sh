#!/bin/sh
# Bounded, read-only inventory. Requires ordinary Linux tools, no Python agent.
set -eu
field() { printf '%s\t%s\n' "$1" "$2"; }
field inventory_mode "${1:-full}"
if [ "${1:-full}" != dynamic ]; then
field hostname "$(hostname)"
field architecture "$(uname -m)"
field kernel "$(uname -r)"
field os "$(awk -F= '$1=="PRETTY_NAME" {gsub(/\042/,"",$2); print $2}' /etc/os-release 2>/dev/null || true)"
field cpu_name "$(awk -F: '/model name|Processor|Hardware/ {gsub(/^[ \t]+/,"",$2); print $2; exit}' /proc/cpuinfo)"
field cpu_count "$(getconf _NPROCESSORS_ONLN)"
field machine_id "$(cat /etc/machine-id 2>/dev/null || true)"
field hardware_model "$(tr -d '\000' </proc/device-tree/model 2>/dev/null || true)"
field l4t "$(head -c 256 /etc/nv_tegra_release 2>/dev/null || true)"
field glibc "$(getconf GNU_LIBC_VERSION 2>/dev/null || true)"
field cuda_version "$(readlink -f /usr/local/cuda 2>/dev/null || true)"
field linger "$(timeout 3 loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || true)"
fi
field cpu_total "$(awk '/^cpu / {for(i=2;i<=9;i++) s+=$i; print s; exit}' /proc/stat)"
field cpu_idle "$(awk '/^cpu / {print $5+$6; exit}' /proc/stat)"
field memory_total "$(awk '/^MemTotal:/ {printf "%.0f",$2*1024}' /proc/meminfo)"
field memory_available "$(awk '/^MemAvailable:/ {printf "%.0f",$2*1024}' /proc/meminfo)"
field swap_used "$(awk '/^SwapTotal:/ {t=$2} /^SwapFree:/ {f=$2} END {printf "%.0f",(t-f)*1024}' /proc/meminfo)"
if command -v nvidia-smi >/dev/null 2>&1; then
  timeout 3 nvidia-smi --query-gpu=index,name,memory.total,memory.free,utilization.gpu --format=csv,noheader,nounits 2>/dev/null | sed 's/^/gpu\t/' || true
fi
for f in /sys/devices/platform/17000000.gpu/load /sys/devices/gpu.0/load; do
  if [ -r "$f" ]; then field gpu_usage "$(awk '{print $1/10}' "$f")"; break; fi
done
