"""Remote scripts are fixed operations; only validated, shell-quoted arguments vary."""

INSTALL_KEY = """set -eu
# LIM_OP install_key
key=$1
umask 077
mkdir -p "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 700 "$HOME/.ssh"
chmod 600 "$HOME/.ssh/authorized_keys"
if ! grep -Fqx -- "$key" "$HOME/.ssh/authorized_keys"; then printf '%s\\n' "$key" >> "$HOME/.ssh/authorized_keys"; fi
"""
SERVICE_STATUS = """set -eu
# LIM_OP service_status
unit=$1
systemctl --user show "$unit" -p LoadState -p ActiveState -p SubState -p MainPID -p ExecMainStatus -p ExecStart -p FragmentPath --no-pager
pid=$(systemctl --user show "$unit" -p MainPID --value)
if [ "$pid" -gt 0 ] && [ -r "/proc/$pid/stat" ]; then
  printf 'BootID=%s\\n' "$(cat /proc/sys/kernel/random/boot_id)"
  printf 'ProcessStart=%s\\n' "$(sed 's/^.*) //' "/proc/$pid/stat" | awk '{print $20}')"
  printf 'Executable=%s\\n' "$(readlink -f "/proc/$pid/exe")"
  printf 'ExecutableSHA256=%s\\n' "$(sha256sum "/proc/$pid/exe" | awk '{print $1}')"
  printf 'RSSBytes=%s\\n' "$(awk '/^VmRSS:/ {printf "%.0f",$2*1024}' "/proc/$pid/status")"
fi
"""
PREPARE = """set -eu
# LIM_OP prepare
rel=$1
owner=$2
base="$HOME/$rel"
if [ -L "$base" ]; then echo 'worker directory is a symlink' >&2; exit 1; fi
if [ -d "$base" ] && [ -f "$base/owner" ] && [ "$(cat "$base/owner")" != "$owner" ]; then echo 'worker ownership mismatch' >&2; exit 1; fi
if [ -d "$base" ] && [ ! -f "$base/owner" ] && [ "$(ls -A "$base")" ]; then echo 'unowned existing directory' >&2; exit 1; fi
umask 077
mkdir -p "$base/releases" "$base/cache"
printf '%s' "$owner" > "$base/owner"
command -v tar >/dev/null
command -v sha256sum >/dev/null
systemctl --user show-environment >/dev/null
printf 'DiskAvailable=%s\\n' "$(df -Pk "$base" | awk 'NR==2 {printf "%.0f",$4*1024}')"
"""
INSTALL = """set -eu
# LIM_OP install
rel=$1; release=$2; digest=$3; unit=$4; bind=$5; port=$6; device=$7; owner=$8; cuda=$9
base="$HOME/$rel"
[ "$(cat "$base/owner")" = "$owner" ]
# Capture the current configuration even if package validation fails before switching.
rm -f "$base/run.previous" "$base/unit.previous"
if [ -f "$base/run.sh" ]; then cp "$base/run.sh" "$base/run.previous"; fi
mkdir -p "$HOME/.config/systemd/user"
if [ -f "$HOME/.config/systemd/user/$unit" ]; then cp "$HOME/.config/systemd/user/$unit" "$base/unit.previous"; fi
actual=$(sha256sum "$base/package.part" | awk '{print $1}')
[ "$actual" = "$digest" ] || { echo 'archive checksum mismatch' >&2; exit 1; }
version="$base/releases/$release-$digest"
stage="$base/releases/.stage-$$"
mkdir "$stage"
trap 'rm -rf "$stage"' EXIT HUP INT TERM
tar --extract --gzip --file "$base/package.part" --directory "$stage" --no-same-owner --no-same-permissions
bin="$stage/bin"
chmod 755 "$bin/ggml-rpc-server"
export LD_LIBRARY_PATH="$bin:$cuda:/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
if ldd "$bin/ggml-rpc-server" | grep -q 'not found'; then echo 'missing RPC runtime libraries' >&2; exit 1; fi
if [ -f "$bin/libggml-cuda.so" ] && ldd "$bin/libggml-cuda.so" | grep -q 'not found'; then echo 'missing CUDA runtime libraries' >&2; exit 1; fi
if [ -d "$version" ]; then
  diff -qr "$stage" "$version" >/dev/null || { echo 'installed release content changed' >&2; exit 1; }
else
  mv "$stage" "$version"
fi
bin="$version/bin"
# Stage new files; previous unit/runner are kept until health succeeds.
{
  printf '#!/bin/sh\\nset -eu\\n'
  printf 'export LD_LIBRARY_PATH="%s:%s:/usr/local/cuda/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\\n' "$bin" "$cuda"
  printf 'export LLAMA_CACHE="%s/cache"\\n' "$base"
  printf 'exec "%s/ggml-rpc-server" --host %s --port %s --device %s --cache\\n' "$bin" "$bind" "$port" "$device"
} > "$base/run.next"
chmod 700 "$base/run.next"
{
  printf '[Unit]\\nDescription=Llama Manager RPC %s\\nAfter=network.target\\n[Service]\\nType=exec\\nExecStart="%%h/%s/run.sh"\\nRestart=on-failure\\nRestartSec=5\\nTimeoutStopSec=15\\nKillMode=control-group\\n[Install]\\nWantedBy=default.target\\n' "$owner" "$rel"
} > "$HOME/.config/systemd/user/$unit.next"
mv "$base/run.next" "$base/run.sh"
mv "$HOME/.config/systemd/user/$unit.next" "$HOME/.config/systemd/user/$unit"
systemctl --user daemon-reload
printf 'UnitSHA256=%s\\n' "$(sha256sum "$HOME/.config/systemd/user/$unit" | awk '{print $1}')"
printf 'RunnerSHA256=%s\\n' "$(sha256sum "$base/run.sh" | awk '{print $1}')"
rm -f "$base/package.part"
"""
ROLLBACK = """set -eu
# LIM_OP rollback
base="$HOME/$1"; unit=$2; owner=$3
[ "$(cat "$base/owner")" = "$owner" ]
if [ -f "$base/run.previous" ]; then mv "$base/run.previous" "$base/run.sh"; fi
if [ -f "$base/unit.previous" ]; then mv "$base/unit.previous" "$HOME/.config/systemd/user/$unit"; else rm -f "$HOME/.config/systemd/user/$unit"; fi
systemctl --user daemon-reload
"""
CHECK_OWNER = """set -eu
# LIM_OP owner
[ "$(cat "$HOME/$1/owner")" = "$2" ] || { echo 'worker ownership mismatch' >&2; exit 1; }
printf 'UnitSHA256=%s\\n' "$(sha256sum "$HOME/.config/systemd/user/$3" | awk '{print $1}')"
printf 'RunnerSHA256=%s\\n' "$(sha256sum "$HOME/$1/run.sh" | awk '{print $1}')"
"""
CLEAN_CACHE = """set -eu
# LIM_OP clean_cache
base="$HOME/$1"; owner=$2
[ "$(cat "$base/owner")" = "$owner" ]
[ -d "$base/cache" ] && [ ! -L "$base/cache" ]
find "$base/cache" -mindepth 1 -maxdepth 1 -type f -delete
find "$base/cache/rpc" -mindepth 1 -maxdepth 1 -type f -delete 2>/dev/null || true
"""
