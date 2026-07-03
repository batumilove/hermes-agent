#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: HERMES_ENV=staging HERMES_HOME=/path/to/staging-home HERMES_PROFILE=gateway-canary staging_cron_smoke.sh

Creates and runs a local-delivery staging cron smoke under the staging Hermes home/profile only. It refuses production homes and non-local delivery.
USAGE
}

for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ "${HERMES_ENV:-}" == "staging" ]] || fail "refusing cron smoke: set HERMES_ENV=staging"
[[ "${HERMES_PROFILE:-}" == "gateway-canary" || "${HERMES_PROFILE:-}" == "skill-lab" ]] || fail "set HERMES_PROFILE=gateway-canary or skill-lab"
[[ -n "${HERMES_HOME:-}" ]] || fail "HERMES_HOME must point to staging-only home"
case "$HERMES_HOME" in
  /home/ubuntu/.hermes|/root/.hermes|"$HOME/.hermes") fail "HERMES_HOME points at a production/default home: $HERMES_HOME" ;;
esac
[[ "$HERMES_HOME" == *staging* || "$HERMES_HOME" == *canary* ]] || fail "HERMES_HOME '$HERMES_HOME' does not look staging/canary"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
HERMES_BIN="${HERMES_BIN:-$REPO_ROOT/venv/bin/hermes}"
if [[ ! -x "$HERMES_BIN" ]]; then
  HERMES_BIN="${HERMES_BIN_FALLBACK:-hermes}"
fi

mkdir -p "$HERMES_HOME/profiles/$HERMES_PROFILE"

cat <<INFO
Hermes staging cron smoke
  repo_root:      $REPO_ROOT
  hermes_bin:     $HERMES_BIN
  HERMES_ENV:     $HERMES_ENV
  HERMES_HOME:    $HERMES_HOME
  HERMES_PROFILE: $HERMES_PROFILE
INFO

if [[ -d "$REPO_ROOT/.git" ]]; then
  git -C "$REPO_ROOT" status --short --branch
  branch="$(git -C "$REPO_ROOT" branch --show-current || true)"
  if [[ "$branch" == "batumi/staging" || "$branch" == "" ]]; then
    :
  elif [[ "$branch" == "batumi/live" && "${HERMES_STAGING_ALLOW_LIVE_BRANCH:-0}" == "1" ]]; then
    echo "WARN: using batumi/live staging checkout fallback; isolation relies on dedicated VM, staging HERMES_HOME, and staging-only profiles." >&2
  else
    fail "checkout branch '$branch' is not batumi/staging"
  fi
fi

"$HERMES_BIN" --version
config_path="$("$HERMES_BIN" --profile "$HERMES_PROFILE" config path)"
case "$config_path" in
  "$HERMES_HOME"/*) ;;
  *) fail "hermes config path is outside staging HERMES_HOME: $config_path" ;;
esac

job_name="staging-cron-smoke-$(date +%Y%m%d%H%M%S)"
marker_dir="$HERMES_HOME/staging-smoke"
marker="$marker_dir/${job_name}.txt"
mkdir -p "$marker_dir"

prompt="This is a staging-only cron smoke. Do not use messaging. Write a concise final response: staging cron smoke ok."

create_output="$("$HERMES_BIN" --profile "$HERMES_PROFILE" cron create "2026-01-01T00:00:00" "$prompt" \
  --name "$job_name" \
  --deliver local)"
printf '%s\n' "$create_output"

job_id="$(printf '%s\n' "$create_output" | grep -Eo 'cron_[A-Za-z0-9_-]+|[0-9a-f]{8,}' | head -n1 || true)"
if [[ -z "$job_id" ]]; then
  echo "WARN: could not parse cron job id from create output; listing jobs for evidence" >&2
  "$HERMES_BIN" --profile "$HERMES_PROFILE" cron list || true
else
  "$HERMES_BIN" --profile "$HERMES_PROFILE" cron run "$job_id" || true
fi

printf 'staging cron smoke requested at %s\njob_name=%s\njob_id=%s\ndeliver=local\n' "$(date -Is)" "$job_name" "${job_id:-unknown}" > "$marker"

grep -R "telegram:\|discord:\|slack:\|signal:\|all" "$HERMES_HOME/cron" 2>/dev/null && fail "found non-local delivery target in staging cron home" || true

test -s "$marker" || fail "marker was not written: $marker"
echo "cron smoke complete; marker: $marker"
