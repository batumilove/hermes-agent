#!/usr/bin/env bash
set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s --stage REVIEWED_COMMIT REVIEWED_TREE | --authorize\n' "$0" >&2
  exit 64
}

[[ $EUID -eq 0 ]] || { printf 'ERROR: root required\n' >&2; exit 77; }
mode=${1:-}
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
source_helper="$repo_root/scripts/staging/hermes_staging_socket_diagnostic.py"
source_artifacts="$repo_root/deploy/staging-diagnostics"
installed_helper=/usr/local/libexec/hermes-staging-diagnostic
state_root=/var/lib/hermes-staging-diagnostics
staged_root="$state_root/staged"
manifest="$state_root/artifact-manifest.json"
sudoers_target=/etc/sudoers.d/hermes-staging-diagnostic
lock=/run/lock/hermes-staging-diagnostic.lock
service=/etc/systemd/system/hermes-staging-diagnostic-recovery.service
timer=/etc/systemd/system/hermes-staging-diagnostic-recovery.timer
tmpfiles=/etc/tmpfiles.d/hermes-staging-diagnostic.conf
staged_sudoers=/var/lib/hermes-staging-diagnostics/staged/hermes-staging-diagnostic.sudoers

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

install_reviewed() {
  local repository_path=$1 source=$2 target=$3 mode_bits=$4 expected actual
  expected=$(git -C "$repo_root" show "$reviewed_commit:$repository_path" | sha256sum | cut -d' ' -f1)
  [[ $expected =~ ^[0-9a-f]{64}$ ]] || { printf 'ERROR: reviewed artifact hash unavailable\n' >&2; exit 1; }
  atomic_install "$source" "$target" "$mode_bits"
  actual=$(sha256sum -- "$target" | cut -d' ' -f1)
  [[ $actual == "$expected" ]] || { printf 'ERROR: reviewed artifact byte mismatch\n' >&2; exit 1; }
}

if [[ $mode == --stage ]]; then
  [[ $# -eq 3 ]] || usage
  reviewed_commit=$2
  reviewed_tree=$3
  [[ $reviewed_commit =~ ^[0-9a-f]{40}$ ]] || { printf 'ERROR: invalid reviewed commit\n' >&2; exit 1; }
  [[ $reviewed_tree =~ ^[0-9a-f]{40}$ ]] || { printf 'ERROR: invalid reviewed tree\n' >&2; exit 1; }
  [[ -z $(git -C "$repo_root" status --porcelain --untracked-files=all) ]] || {
    printf 'ERROR: source checkout is not clean\n' >&2; exit 1;
  }
  [[ $(git -C "$repo_root" rev-parse HEAD) == "$reviewed_commit" ]] || {
    printf 'ERROR: reviewed commit mismatch\n' >&2; exit 1;
  }
  [[ $(git -C "$repo_root" rev-parse 'HEAD^{tree}') == "$reviewed_tree" ]] || {
    printf 'ERROR: reviewed tree mismatch\n' >&2; exit 1;
  }
  getent group hermes-deploy >/dev/null || { printf 'ERROR: hermes-deploy group missing\n' >&2; exit 1; }
  install -o root -g root -m 0755 -d /usr/local/libexec /etc/systemd/system /etc/tmpfiles.d
  install -o root -g root -m 0700 -d "$state_root" "$staged_root"

  install_reviewed scripts/staging/hermes_staging_socket_diagnostic.py \
    "$source_helper" "$staged_root/helper" 0755
  install_reviewed deploy/staging-diagnostics/hermes-staging-diagnostic.sudoers \
    "$source_artifacts/hermes-staging-diagnostic.sudoers" "$staged_sudoers" 0600
  install_reviewed deploy/staging-diagnostics/hermes-staging-diagnostic-recovery.service \
    "$source_artifacts/hermes-staging-diagnostic-recovery.service" "$staged_root/service" 0644
  install_reviewed deploy/staging-diagnostics/hermes-staging-diagnostic-recovery.timer \
    "$source_artifacts/hermes-staging-diagnostic-recovery.timer" "$staged_root/timer" 0644
  install_reviewed deploy/staging-diagnostics/hermes-staging-diagnostic.tmpfiles \
    "$source_artifacts/hermes-staging-diagnostic.tmpfiles" "$staged_root/tmpfiles" 0644
  atomic_install "$staged_root/tmpfiles" "$tmpfiles" 0644
  systemd-tmpfiles --create "$tmpfiles"
  [[ -f $lock && ! -L $lock && $(stat -c '%U:%G:%a:%h:%s' -- "$lock") == root:hermes-deploy:660:1:0 ]] || {
    printf 'ERROR: shared lock metadata mismatch\n' >&2; exit 1;
  }
  exec 9<>"$lock"
  flock -w 60 9 || { printf 'ERROR: shared lock busy\n' >&2; exit 1; }

  atomic_install "$staged_root/helper" "$installed_helper" 0755
  atomic_install "$staged_root/service" "$service" 0644
  atomic_install "$staged_root/timer" "$timer" 0644

  python3 - "$manifest.tmp" "$reviewed_commit" "$reviewed_tree" \
    "$installed_helper" "$staged_sudoers" "$service" "$timer" "$tmpfiles" "$lock" <<'PY'
import hashlib, json, os, pathlib, stat, sys
output, reviewed_commit, reviewed_tree, *paths = sys.argv[1:]
names = ("helper", "sudoers", "service", "timer", "tmpfiles", "lock")
artifacts = {}
for name, raw_path in zip(names, paths, strict=True):
    path = pathlib.Path(raw_path)
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != 0:
        raise SystemExit(f"unsafe staged {name}")
    data = path.read_bytes()
    artifacts[name] = {
        "path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
        "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode),
    }
payload = {"version": 1, "reviewed_commit": reviewed_commit, "reviewed_tree": reviewed_tree, "artifacts": artifacts}
pathlib.Path(output).write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
  chown root:root "$manifest.tmp"
  chmod 0600 "$manifest.tmp"
  sync -f "$manifest.tmp"
  mv -fT -- "$manifest.tmp" "$manifest"
  sync -f "$state_root"
  systemctl daemon-reload
  printf '%s\n' 'Staged reviewed root-owned artifacts and shared lock; sudo authorization and timer activation remain disabled.'
  printf '%s\n' 'SECURITY: containment remains FAIL while hermes-deploy retains docker group access (root-equivalent).'
  exit 0
fi

[[ $mode == --authorize && $# -eq 1 ]] || usage
command -v visudo >/dev/null || { printf 'ERROR: visudo unavailable\n' >&2; exit 1; }
[[ -f $manifest && ! -L $manifest && $(stat -c '%U:%G:%a:%h' -- "$manifest") == root:root:600:1 ]] || {
  printf 'ERROR: staged artifact manifest missing or unsafe\n' >&2; exit 1;
}

# Authorization consumes only root-owned staged/installed paths named by the
# manifest. It never reopens the mutable checkout's sudoers template.
helper_digest=$(python3 - "$manifest" <<'PY'
import hashlib, json, pathlib, re, stat, sys
manifest_path = pathlib.Path(sys.argv[1])
value = json.loads(manifest_path.read_text(encoding="utf-8"))
if set(value) != {"version", "reviewed_commit", "reviewed_tree", "artifacts"} or value["version"] != 1:
    raise SystemExit("invalid artifact manifest schema")
if not re.fullmatch(r"[0-9a-f]{40}", value["reviewed_commit"]) or not re.fullmatch(r"[0-9a-f]{40}", value["reviewed_tree"]):
    raise SystemExit("invalid reviewed commit/tree")
expected_names = {"helper", "sudoers", "service", "timer", "tmpfiles", "lock"}
if set(value["artifacts"]) != expected_names:
    raise SystemExit("invalid artifact set")
for name, record in value["artifacts"].items():
    if set(record) != {"path", "sha256", "uid", "gid", "mode"}:
        raise SystemExit(f"invalid {name} record")
    path = pathlib.Path(record["path"])
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise SystemExit(f"unsafe {name}")
    actual = {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "uid": st.st_uid, "gid": st.st_gid, "mode": stat.S_IMODE(st.st_mode),
    }
    if actual != {key: record[key] for key in actual} or st.st_uid != 0:
        raise SystemExit(f"staged {name} verification failed")
print(value["artifacts"]["helper"]["sha256"])
PY
)
[[ $helper_digest =~ ^[0-9a-f]{64}$ ]] || { printf 'ERROR: invalid helper digest\n' >&2; exit 1; }

temporary=/etc/sudoers.d/.hermes-staging-diagnostic.$$.tmp
trap 'rm -f -- "$temporary"' EXIT
python3 - "$staged_sudoers" "$temporary" "$helper_digest" <<'PY'
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
printf '%s\n' 'Authorized exact no-argument helper digest from verified reviewed artifacts; recovery timer remains disabled.'
printf '%s\n' 'SECURITY: containment remains FAIL while hermes-deploy retains docker group access (root-equivalent).'
