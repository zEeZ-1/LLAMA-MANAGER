"""Bounded RPC discovery and identity-checked removal; no remote agent."""

DISCOVER = r"""set -eu
# LIM_OP discover_rpc
printf 'boot\t%s\n' "$(cat /proc/sys/kernel/random/boot_id)"
printf 'uid\t%s\n' "$(id -u)"
ss -ltnpH | head -c 4096 | while IFS= read -r line; do printf 'listen\t%s\n' "$line"; done
ss -tnpH state established | head -c 8192 | while IFS= read -r line; do
  printf 'connection\t%s\n' "$line"
done
ps -eo pid=,comm= | awk '$2 == "ggml-rpc-server" {print $1}' | head -n 16 | while read -r pid; do
  [ -r "/proc/$pid/stat" ] || continue
  start=$(sed 's/^.*) //' "/proc/$pid/stat" | awk '{print $20}')
  uid=$(awk '/^Uid:/ {print $2}' "/proc/$pid/status")
  rss=$(awk '/^VmRSS:/ {printf "%.0f",$2*1024}' "/proc/$pid/status")
  exe=$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)
  digest=$(sha256sum "/proc/$pid/exe" 2>/dev/null | awk '{print $1}' || true)
  printf 'process\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$pid" "$start" "$uid" "${rss:-0}" \
    "$(printf '%s' "$exe" | head -c 512 | base64 -w0)" "$digest" \
    "$(head -c 512 "/proc/$pid/cgroup" 2>/dev/null | base64 -w0 || true)" \
    "$(head -c 1024 "/proc/$pid/cmdline" 2>/dev/null | base64 -w0 || true)"
done
"""

REMOVE_EXTERNAL = r"""set -eu
# LIM_OP remove_external_rpc
pid=$1; start=$2; boot=$3; digest=$4; unit=$5; signature=$6; force=$7
if [ "$pid" -gt 0 ]; then [ "$(cat /proc/sys/kernel/random/boot_id)" = "$boot" ] || { echo 'rpc_identity_changed' >&2; exit 1; }; fi
if [ "$pid" -gt 0 ] && [ -r "/proc/$pid/stat" ]; then
  [ "$(sed 's/^.*) //' "/proc/$pid/stat" | awk '{print $20}')" = "$start" ] || { echo 'rpc_identity_changed' >&2; exit 1; }
  [ "$(cat "/proc/$pid/comm")" = ggml-rpc-server ] || { echo 'rpc_identity_changed' >&2; exit 1; }
  [ "$(awk '/^Uid:/ {print $2}' "/proc/$pid/status")" = "$(id -u)" ] || { echo 'rpc_permission_required' >&2; exit 1; }
  [ "$(sha256sum "/proc/$pid/exe" | awk '{print $1}')" = "$digest" ] || { echo 'rpc_identity_changed' >&2; exit 1; }
  if [ "$force" != yes ] && ss -tnpH state established | grep -F "pid=$pid," >/dev/null; then
    echo 'rpc_in_use' >&2; exit 1
  fi
fi
if [ -n "$unit" ]; then
  case "$unit" in *[!A-Za-z0-9_.@-]*) echo 'rpc_identity_changed' >&2; exit 1;; esac
  current=$(systemctl --user show "$unit" -p ExecStart --value)
  [ "$current" = "$signature" ] || { echo 'rpc_identity_changed' >&2; exit 1; }
  main=$(systemctl --user show "$unit" -p MainPID --value)
  [ "$main" = "$pid" ] || [ "$main" = 0 ] || { echo 'rpc_identity_changed' >&2; exit 1; }
  fragment=$(systemctl --user show "$unit" -p FragmentPath --value)
  systemctl --user stop "$unit"
  systemctl --user disable "$unit"
  if [ "$(readlink -f "$fragment")" = "$HOME/.config/systemd/user/$unit" ] && [ ! -L "$fragment" ]; then
    rm -f -- "$fragment"
    systemctl --user daemon-reload
  fi
elif [ "$pid" -gt 0 ] && [ -r "/proc/$pid/stat" ]; then
  kill -TERM "$pid"
  i=0
  while [ -r "/proc/$pid/stat" ] && [ "$i" -lt 25 ]; do
    [ "$(sed 's/^.*) //' "/proc/$pid/stat" | awk '{print $20}')" = "$start" ] || break
    [ "$(sed 's/^.*) //' "/proc/$pid/stat" | awk '{print $1}')" != Z ] || break
    sleep 0.2; i=$((i+1))
  done
  if [ -r "/proc/$pid/stat" ] && [ "$(sed 's/^.*) //' "/proc/$pid/stat" | awk '{print $20}')" = "$start" ]; then kill -KILL "$pid"; fi
fi
"""

UNINSTALL = r"""set -eu
# LIM_OP uninstall_rpc
base="$HOME/$1"; unit=$2; owner=$3
[ ! -L "$base" ] && [ "$(cat "$base/owner")" = "$owner" ]
systemctl --user stop "$unit"
systemctl --user disable "$unit"
rm -f -- "$HOME/.config/systemd/user/$unit"
systemctl --user daemon-reload
rm -rf -- "$base"
"""
