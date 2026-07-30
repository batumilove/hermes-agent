#!/usr/bin/env bash
set -euo pipefail
umask 077

mode=${1:-}
txn=${2:-}
candidate_dir=${3:-}

container=prometheus
destination=/opt/monitoring/prometheus/rules/hermes_provider_telemetry.rules.yml
backup_root=/var/backups/hermes-provider-telemetry
lock_path=/run/lock/hermes-provider-telemetry-prometheus.lock
prometheus_url=http://192.168.10.121:9090
alert_name=HermesProviderTelemetryCounterRegression

[[ $EUID -eq 0 ]] || { echo 'ERROR: root required' >&2; exit 77; }
[[ $mode =~ ^(deploy|verify|rollback)$ ]] || { echo 'ERROR: invalid mode' >&2; exit 64; }
[[ $txn =~ ^[0-9]+-[0-9]+$ ]] || { echo 'ERROR: invalid transaction id' >&2; exit 64; }
backup_dir="$backup_root/$txn"

install -d -o root -g root -m 0700 "$backup_root"
exec 9>"$lock_path"
flock -n 9 || { echo 'ERROR: Prometheus telemetry deployment lock busy' >&2; exit 75; }

assert_prometheus_ready() {
  [[ $(docker inspect --format '{{.State.Running}}' "$container") == true ]]
  curl -fsS "$prometheus_url/-/ready" | grep -q 'Prometheus Server is Ready'
}

validate_candidate() {
  local candidate=$1
  [[ -f $candidate && ! -L $candidate ]]
  docker cp "$candidate" "$container:/tmp/hermes_provider_telemetry.candidate.yml"
  trap 'docker exec "$container" rm -f /tmp/hermes_provider_telemetry.candidate.yml >/dev/null 2>&1 || true' RETURN
  docker exec "$container" promtool check rules /tmp/hermes_provider_telemetry.candidate.yml
  docker exec "$container" rm -f /tmp/hermes_provider_telemetry.candidate.yml
  trap - RETURN
}

reload_prometheus() {
  curl -fsS -X POST "$prometheus_url/-/reload" >/dev/null
  for _ in $(seq 1 30); do
    if assert_prometheus_ready; then
      return 0
    fi
    sleep 2
  done
  echo 'ERROR: Prometheus failed readiness after reload' >&2
  return 1
}

assert_rule_loaded() {
  curl -fsS "$prometheus_url/api/v1/rules" | python3 -c '
import json, sys
alert = sys.argv[1]
data = json.load(sys.stdin)
if data.get("status") != "success":
    raise SystemExit("Prometheus rules API did not return success")
found = []
for group in data.get("data", {}).get("groups", []):
    for rule in group.get("rules", []):
        if rule.get("name") == alert:
            found.append(rule)
if len(found) != 1:
    raise SystemExit(f"expected exactly one {alert} rule, found {len(found)}")
print(f"PROMETHEUS_RULE_LOADED alert={alert}")
' "$alert_name"
}

assert_installed_hash() {
  [[ -s $backup_dir/installed.sha256 ]]
  local actual expected
  actual=$(sha256sum "$destination" | awk '{print $1}')
  expected=$(cat "$backup_dir/installed.sha256")
  [[ $actual == "$expected" ]] || {
    echo "ERROR: installed rule hash mismatch expected=$expected actual=$actual" >&2
    return 1
  }
}

rollback() {
  local rollback_failed=0
  set +e
  if [[ -f $backup_dir/rules.previous ]]; then
    python3 - "$backup_dir/rules.previous" "$destination" <<'PY' || rollback_failed=1
import os, shutil, sys, tempfile
source, destination = sys.argv[1:]
directory = os.path.dirname(destination)
fd, temporary = tempfile.mkstemp(prefix=".hermes-provider-telemetry.rollback.", dir=directory)
try:
    with os.fdopen(fd, "wb") as out, open(source, "rb") as src:
        shutil.copyfileobj(src, out)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, destination)
    dirfd = os.open(directory, os.O_DIRECTORY)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
  elif [[ -f $backup_dir/rule-absent ]]; then
    rm -f -- "$destination" || rollback_failed=1
  else
    rollback_failed=1
  fi
  reload_prometheus || rollback_failed=1
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
  assert_prometheus_ready
  assert_installed_hash
  docker cp "$destination" "$container:/tmp/hermes_provider_telemetry.installed.yml"
  docker exec "$container" promtool check rules /tmp/hermes_provider_telemetry.installed.yml
  docker exec "$container" rm -f /tmp/hermes_provider_telemetry.installed.yml
  assert_rule_loaded
  printf 'VERIFIED\n' >"$backup_dir/status"
  echo "PROMETHEUS_TELEMETRY_VERIFIED txn=$txn alert=$alert_name"
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
    [[ $candidate_dir == "/tmp/hermes-telemetry-$txn" ]] || {
      echo 'ERROR: candidate path outside fixed transaction root' >&2; exit 64;
    }
    candidate="$candidate_dir/prometheus/hermes_provider_telemetry.rules.yml"
    manifest="$candidate_dir/manifest.sha256"
    [[ -f $candidate && -f $manifest && ! -L $candidate ]]
    (
      cd "$candidate_dir"
      grep -E '  prometheus/hermes_provider_telemetry\.rules\.yml$' manifest.sha256 \
        > prometheus-manifest.sha256
      [[ $(wc -l < prometheus-manifest.sha256) -eq 1 ]]
      sha256sum -c prometheus-manifest.sha256
    )
    assert_prometheus_ready
    validate_candidate "$candidate"

    install -d -o root -g root -m 0700 "$backup_dir"
    if [[ -f $destination ]]; then
      install -o root -g root -m 0600 "$destination" "$backup_dir/rules.previous"
    else
      : >"$backup_dir/rule-absent"
    fi
    sha256sum "$candidate" | awk '{print $1}' >"$backup_dir/installed.sha256"
    printf 'PREPARED\n' >"$backup_dir/status"

    trap on_error ERR
    python3 - "$candidate" "$destination" <<'PY'
import os, shutil, sys, tempfile
source, destination = sys.argv[1:]
directory = os.path.dirname(destination)
fd, temporary = tempfile.mkstemp(prefix=".hermes-provider-telemetry.install.", dir=directory)
try:
    with os.fdopen(fd, "wb") as out, open(source, "rb") as src:
        shutil.copyfileobj(src, out)
        out.flush()
        os.fsync(out.fileno())
    os.chmod(temporary, 0o644)
    os.replace(temporary, destination)
    dirfd = os.open(directory, os.O_DIRECTORY)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
PY
    reload_prometheus
    verify_transaction
    printf 'COMMITTED\n' >"$backup_dir/status"
    trap - ERR
    ;;
esac
