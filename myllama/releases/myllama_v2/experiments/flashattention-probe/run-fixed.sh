#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
gpu=/sys/class/devfreq/17000000.gpu
prev=$(cat "$gpu/min_freq")
max=$(cat "$gpu/max_freq")
sudo -n systemd-run --quiet --unit=llama-fa2-clock-restore --on-active=240s \
    /bin/sh -c 'printf "%s\n" "$1" > "$2"' sh "$prev" "$gpu/min_freq"
restore() {
    printf '%s\n' "$prev" | sudo -n tee "$gpu/min_freq" >/dev/null
    sudo -n systemctl stop llama-fa2-clock-restore.timer
}
trap restore EXIT
printf '%s\n' "$max" | sudo -n tee "$gpu/min_freq" >/dev/null
test "$(cat "$gpu/min_freq")" = "$max"
./probe "$@"
