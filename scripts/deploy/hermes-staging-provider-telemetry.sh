#!/usr/bin/env bash
set -euo pipefail
umask 077

mode=${1:-}
txn=${2:-}
expected_source_sha=${3:-}
candidate_dir=${4:-}

container=hermes-batumi-staging-gateway
data_root=/home/hermes-staging/.hermes-staging
backup_root=/opt/hermes-compose/staging/telemetry-backups
lock_path=/run/lock/hermes-staging-diagnostic.lock
plugin_path=/opt/data/plugins/provider_telemetry
metrics_path=/opt/data/state/prometheus/hermes_provider_telemetry.prom
writer_lock_path=/opt/data/state/prometheus/hermes_provider_telemetry.prom.lock

[[ $mode =~ ^(deploy|verify|rollback)$ ]] || { echo 'ERROR: invalid mode' >&2; exit 64; }
[[ $txn =~ ^[0-9]+-[0-9]+$ ]] || { echo 'ERROR: invalid transaction id' >&2; exit 64; }
[[ $expected_source_sha =~ ^[0-9a-f]{40}$ ]] || { echo 'ERROR: invalid expected source SHA' >&2; exit 64; }
backup_dir="$backup_root/$txn"

exec 9>"$lock_path"
flock -n 9 || { echo 'ERROR: staging deployment/diagnostic lock busy' >&2; exit 75; }

container_running() {
  [[ $(docker inspect --format '{{.State.Running}}' "$container") == true ]]
}

assert_data_mount() {
  local actual
  actual=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/opt/data"}}{{.Source}}{{end}}{{end}}' "$container")
  [[ $actual == "$data_root" ]] || {
    echo "ERROR: Hermes data mount mismatch expected=$data_root actual=${actual:-missing}" >&2
    return 1
  }
}

assert_source_identity() {
  local actual
  actual=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container" \
    | sed -n 's/^HERMES_SOURCE_SHA=//p' | head -n1)
  [[ $actual == "$expected_source_sha" ]] || {
    echo "ERROR: Hermes source mismatch expected=$expected_source_sha actual=${actual:-missing}" >&2
    return 1
  }
}

wait_main_up() {
  local health
  for _ in $(seq 1 60); do
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container")
    if [[ $health == healthy ]] && docker exec "$container" \
      /command/s6-svstat /run/service/main-hermes 2>/dev/null | grep -q '^up '; then
      return 0
    fi
    sleep 2
  done
  echo 'ERROR: Hermes gateway failed to become healthy' >&2
  return 1
}

stop_main() {
  docker exec -u 0 "$container" /command/s6-svc -d /run/service/main-hermes
  for _ in $(seq 1 30); do
    if docker exec "$container" /command/s6-svstat /run/service/main-hermes 2>/dev/null | grep -q '^down '; then
      return 0
    fi
    sleep 1
  done
  echo 'ERROR: Hermes main service did not stop' >&2
  return 1
}

start_main() {
  docker exec -u 0 "$container" /command/s6-svc -u /run/service/main-hermes
  wait_main_up
}

snapshot_counters() {
  local output=$1
  docker exec "$container" python3 - "$metrics_path" >"$output" <<'PY'
import json, re, sys
from pathlib import Path
path = Path(sys.argv[1])
counters = {}
if path.exists():
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#"):
            continue
        metric, value = raw.rsplit(None, 1)
        name = metric.split("{", 1)[0]
        if name.endswith("_total") or name.endswith("_sum") or name.endswith("_count"):
            if not re.fullmatch(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value):
                raise SystemExit(f"invalid counter value for {metric}")
            counters[metric] = float(value)
print(json.dumps(counters, sort_keys=True, separators=(",", ":")))
PY
}

assert_counters_monotonic() {
  local before=$1 after
  after=$(mktemp)
  trap 'rm -f -- "$after"' RETURN
  snapshot_counters "$after"
  python3 - "$before" "$after" <<'PY'
import json, sys
before = json.load(open(sys.argv[1], encoding="utf-8"))
after = json.load(open(sys.argv[2], encoding="utf-8"))
for series, old in before.items():
    if series not in after:
        raise SystemExit(f"counter disappeared: {series}")
    if after[series] < old:
        raise SystemExit(f"counter decreased: {series}: {old} -> {after[series]}")
print(f"COUNTERS_MONOTONIC series={len(before)}")
PY
  rm -f -- "$after"
  trap - RETURN
}

assert_writer_owner() {
  docker exec "$container" python3 - "$writer_lock_path" <<'PY'
import fcntl, os, stat, sys
path = sys.argv[1]
st = os.stat(path)
if stat.S_IMODE(st.st_mode) != 0o600:
    raise SystemExit(f"writer lock mode is {oct(stat.S_IMODE(st.st_mode))}, expected 0o600")
with open(path, "r+", encoding="utf-8") as lock_file:
    raw = lock_file.read().strip()
    if not raw.isdigit() or not os.path.isdir(f"/proc/{raw}"):
        raise SystemExit("writer lock PID is missing or not live")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"WRITER_OWNER_OK pid={raw}")
    else:
        raise SystemExit("writer lock is not held by the telemetry owner")
PY
}

assert_metrics_up() {
  for _ in $(seq 1 30); do
    if docker exec "$container" grep -qx 'hermes_provider_telemetry_up 1' "$metrics_path" 2>/dev/null; then
      return 0
    fi
    sleep 2
  done
  echo 'ERROR: provider telemetry did not publish up=1' >&2
  return 1
}

assert_plugin_hashes() {
  local manifest=$backup_dir/installed-manifest.sha256
  [[ -s $manifest ]]
  docker exec "$container" sha256sum "$plugin_path/__init__.py" "$plugin_path/plugin.yaml" \
    | sed "s#  $plugin_path/#  plugin/#" >"$backup_dir/current-manifest.sha256"
  diff -u "$manifest" "$backup_dir/current-manifest.sha256"
}

rollback() {
  local restart=0 rollback_failed=0
  set +e
  if ! container_running; then
    rollback_failed=1
  elif ! stop_main; then
    rollback_failed=1
  else
    restart=1
    docker exec -u 0 "$container" rm -rf -- \
      "/opt/data/plugins/.provider_telemetry.incoming-$txn" \
      "/opt/data/plugins/.provider_telemetry.failed-$txn" || rollback_failed=1
    if [[ -d $backup_dir/provider_telemetry ]]; then
      docker exec -u 0 "$container" rm -rf -- "$plugin_path" || rollback_failed=1
      docker cp "$backup_dir/provider_telemetry" "$container:/opt/data/plugins/.provider_telemetry.restore-$txn" \
        || rollback_failed=1
      docker exec -u 0 "$container" python3 - "$txn" <<'PY' || rollback_failed=1
import os, sys
base = "/opt/data/plugins"
txn = sys.argv[1]
os.replace(f"{base}/.provider_telemetry.restore-{txn}", f"{base}/provider_telemetry")
PY
      docker exec -u 0 "$container" chown -R 1001:1001 "$plugin_path" || rollback_failed=1
    elif [[ -f $backup_dir/plugin-absent ]]; then
      docker exec -u 0 "$container" rm -rf -- "$plugin_path" || rollback_failed=1
    else
      rollback_failed=1
    fi
    [[ $restart == 1 ]] && start_main || rollback_failed=1
  fi
  if [[ $rollback_failed == 1 ]]; then
    printf 'ROLLBACK_FAILED\n' >"$backup_dir/status"
    echo 'ERROR: ROLLBACK_FAILED; manual recovery required' >&2
    set -e
    return 1
  fi
  printf 'ROLLED_BACK\n' >"$backup_dir/status"
  set -e
}

on_error() {
  local rc=$?
  trap - ERR
  if ! rollback; then
    exit 70
  fi
  exit "$rc"
}

verify_transaction() {
  container_running
  assert_source_identity
  assert_data_mount
  wait_main_up
  assert_plugin_hashes
  assert_metrics_up
  assert_writer_owner
  assert_counters_monotonic "$backup_dir/counters.before.json"
  printf 'VERIFIED\n' >"$backup_dir/status"
  echo "HERMES_TELEMETRY_VERIFIED txn=$txn source=$expected_source_sha"
}

case $mode in
  rollback)
    [[ -d $backup_dir ]] || { echo 'ERROR: transaction backup missing' >&2; exit 66; }
    rollback
    ;;
  verify)
    [[ -d $backup_dir ]] || { echo 'ERROR: transaction backup missing' >&2; exit 66; }
    verify_transaction
    ;;
  deploy)
    [[ $candidate_dir == "/opt/hermes-compose/staging/.incoming-telemetry-$txn" ]] || {
      echo 'ERROR: candidate path outside fixed transaction root' >&2; exit 64;
    }
    [[ -d $candidate_dir/plugin && -f $candidate_dir/manifest.sha256 ]] || {
      echo 'ERROR: candidate files missing' >&2; exit 66;
    }
    [[ ! -L $candidate_dir/plugin/__init__.py && ! -L $candidate_dir/plugin/plugin.yaml ]]
    mkdir -p "$backup_dir"
    chmod 0700 "$backup_dir"
    (
      cd "$candidate_dir"
      grep -E '  plugin/(__init__\.py|plugin\.yaml)$' manifest.sha256 > plugin-manifest.sha256
      [[ $(wc -l < plugin-manifest.sha256) -eq 2 ]]
      sha256sum -c plugin-manifest.sha256
      cp plugin-manifest.sha256 "$backup_dir/installed-manifest.sha256"
    )
    container_running
    assert_source_identity
    assert_data_mount
    snapshot_counters "$backup_dir/counters.before.json"
    if docker exec "$container" test -d "$plugin_path"; then
      docker cp "$container:$plugin_path" "$backup_dir/provider_telemetry"
    else
      : >"$backup_dir/plugin-absent"
    fi
    printf 'PREPARED\n' >"$backup_dir/status"

    docker exec -u 0 "$container" rm -rf -- "/opt/data/plugins/.provider_telemetry.incoming-$txn"
    docker cp "$candidate_dir/plugin" "$container:/opt/data/plugins/.provider_telemetry.incoming-$txn"
    docker exec -u 0 "$container" chown -R 1001:1001 "/opt/data/plugins/.provider_telemetry.incoming-$txn"
    docker exec -u 0 "$container" chmod 0755 "/opt/data/plugins/.provider_telemetry.incoming-$txn"
    docker exec -u 0 "$container" chmod 0644 \
      "/opt/data/plugins/.provider_telemetry.incoming-$txn/__init__.py" \
      "/opt/data/plugins/.provider_telemetry.incoming-$txn/plugin.yaml"

    trap on_error ERR
    stop_main
    docker exec -u 0 "$container" python3 - "$txn" <<'PY'
import os, shutil, sys
base = "/opt/data/plugins"
txn = sys.argv[1]
target = f"{base}/provider_telemetry"
incoming = f"{base}/.provider_telemetry.incoming-{txn}"
previous = f"{base}/.provider_telemetry.previous-{txn}"
if os.path.lexists(previous):
    if os.path.isdir(previous) and not os.path.islink(previous):
        shutil.rmtree(previous)
    else:
        os.unlink(previous)
if os.path.lexists(target):
    os.replace(target, previous)
os.replace(incoming, target)
PY
    start_main
    verify_transaction
    docker exec -u 0 "$container" rm -rf -- "/opt/data/plugins/.provider_telemetry.previous-$txn"
    printf 'COMMITTED\n' >"$backup_dir/status"
    trap - ERR
    ;;
esac
