#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s --stage|--authorize\n' "$0" >&2
  exit 64
}

[[ $# -eq 1 ]] || usage
[[ $EUID -eq 0 ]] || { printf 'ERROR: root required\n' >&2; exit 77; }
mode=$1
[[ $mode == --stage || $mode == --authorize ]] || usage

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
source_helper="$repo_root/scripts/staging/hermes_staging_socket_diagnostic.py"
source_artifacts="$repo_root/deploy/staging-diagnostics"
installed_helper=/usr/local/libexec/hermes-staging-diagnostic
state_root=/var/lib/hermes-staging-diagnostics
installed_digest="$state_root/installed-helper.sha256"
sudoers_target=/etc/sudoers.d/hermes-staging-diagnostic

atomic_install() {
  local source=$1 target=$2 mode_bits=$3
  local directory temporary
  directory=$(dirname -- "$target")
  temporary="$directory/.hermes-staging-diagnostic.$$.tmp"
  install -o root -g root -m "$mode_bits" -- "$source" "$temporary"
  sync -f "$temporary"
  mv -fT -- "$temporary" "$target"
  sync -f "$directory"
}

if [[ $mode == --stage ]]; then
  install -o root -g root -m 0755 -d /usr/local/libexec
  install -o root -g root -m 0755 -d /etc/systemd/system
  install -o root -g root -m 0700 -d "$state_root"
  atomic_install "$source_helper" "$installed_helper" 0755
  atomic_install "$source_artifacts/hermes-staging-diagnostic-recovery.service" \
    /etc/systemd/system/hermes-staging-diagnostic-recovery.service 0644
  atomic_install "$source_artifacts/hermes-staging-diagnostic-recovery.timer" \
    /etc/systemd/system/hermes-staging-diagnostic-recovery.timer 0644
  digest=$(sha256sum -- "$installed_helper" | cut -d' ' -f1)
  printf '%s\n' "$digest" >"$installed_digest.tmp"
  chmod 0600 "$installed_digest.tmp"
  chown root:root "$installed_digest.tmp"
  sync -f "$installed_digest.tmp"
  mv -fT -- "$installed_digest.tmp" "$installed_digest"
  sync -f "$state_root"
  systemctl daemon-reload
  printf '%s\n' 'Staged dormant helper and recovery units; sudo authorization and timer activation remain disabled.'
  printf '%s\n' 'SECURITY: containment remains FAIL while hermes-deploy retains docker group access (root-equivalent).'
  exit 0
fi

command -v visudo >/dev/null || { printf 'ERROR: visudo unavailable\n' >&2; exit 1; }
[[ -f $installed_helper && ! -L $installed_helper ]] || { printf 'ERROR: staged helper missing\n' >&2; exit 1; }
[[ $(stat -c '%U:%G:%a' -- "$installed_helper") == root:root:755 ]] || {
  printf 'ERROR: staged helper metadata mismatch\n' >&2; exit 1;
}
[[ -f $installed_digest && ! -L $installed_digest ]] || { printf 'ERROR: staged digest missing\n' >&2; exit 1; }
expected_digest=$(<"$installed_digest")
[[ $expected_digest =~ ^[0-9a-f]{64}$ ]] || { printf 'ERROR: invalid staged digest\n' >&2; exit 1; }
actual_digest=$(sha256sum -- "$installed_helper" | cut -d' ' -f1)
[[ $actual_digest == "$expected_digest" ]] || { printf 'ERROR: staged helper digest mismatch\n' >&2; exit 1; }

temporary=/etc/sudoers.d/.hermes-staging-diagnostic.$$.tmp
trap 'rm -f -- "$temporary"' EXIT
python3 - "$source_artifacts/hermes-staging-diagnostic.sudoers" "$temporary" "$actual_digest" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1])
target = Path(sys.argv[2])
digest = sys.argv[3]
text = source.read_text(encoding="utf-8")
if text.count("__HELPER_SHA256__") != 1:
    raise SystemExit("sudoers digest placeholder mismatch")
target.write_text(text.replace("__HELPER_SHA256__", digest), encoding="utf-8")
PY
chown root:root "$temporary"
chmod 0440 "$temporary"
visudo -c -f "$temporary"
mv -fT -- "$temporary" "$sudoers_target"
sync -f /etc/sudoers.d
trap - EXIT
printf '%s\n' 'Authorized exact no-argument helper digest; recovery timer remains disabled pending separate activation.'
printf '%s\n' 'SECURITY: containment remains FAIL while hermes-deploy retains docker group access (root-equivalent).'
