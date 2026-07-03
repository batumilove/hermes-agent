#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: HERMES_ENV=staging HERMES_HOME=/path/to/staging-home HERMES_PROFILE=gateway-canary staging_gateway_smoke.sh [--send-staging-telegram]

Runs staging-only Hermes gateway checks. By default it does not send Telegram messages and does not start/restart gateways.
USAGE
}

SEND_TELEGRAM=0
for arg in "$@"; do
  case "$arg" in
    --send-staging-telegram) SEND_TELEGRAM=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ "${HERMES_ENV:-}" == "staging" ]] || fail "refusing gateway smoke: set HERMES_ENV=staging"
[[ "${HERMES_PROFILE:-}" == "gateway-canary" ]] || fail "refusing gateway smoke: set HERMES_PROFILE=gateway-canary"
[[ -n "${HERMES_HOME:-}" ]] || fail "HERMES_HOME must point to a staging-only home"
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
Hermes staging gateway smoke
  repo_root:      $REPO_ROOT
  hermes_bin:     $HERMES_BIN
  HERMES_ENV:     $HERMES_ENV
  HERMES_HOME:    $HERMES_HOME
  HERMES_PROFILE: $HERMES_PROFILE
  send_telegram:  $SEND_TELEGRAM
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
"$HERMES_BIN" --profile "$HERMES_PROFILE" config path | grep -F "$HERMES_HOME" >/dev/null \
  || fail "hermes config path did not resolve inside staging HERMES_HOME"
"$HERMES_BIN" --profile "$HERMES_PROFILE" status || true
"$HERMES_BIN" --profile "$HERMES_PROFILE" doctor || true

# Validate gateway CLI is importable/configurable without starting or restarting any service.
python_bin="${PYTHON_BIN:-$REPO_ROOT/venv/bin/python}"
if [[ -x "$python_bin" ]]; then
  "$python_bin" - <<'PY'
import importlib
for name in ("hermes_cli.main", "gateway.run"):
    importlib.import_module(name)
print("gateway imports ok")
PY
else
  echo "WARN: repo venv python not found at $python_bin; skipped import smoke" >&2
fi

if [[ "$SEND_TELEGRAM" == "1" ]]; then
  [[ -n "${STAGING_TELEGRAM_BOT_TOKEN:-}" ]] || fail "STAGING_TELEGRAM_BOT_TOKEN is required for --send-staging-telegram"
  [[ -n "${STAGING_TELEGRAM_CHAT_ID:-}" ]] || fail "STAGING_TELEGRAM_CHAT_ID is required for --send-staging-telegram"
  [[ -z "${TELEGRAM_BOT_TOKEN:-}" ]] || fail "TELEGRAM_BOT_TOKEN is set; refusing possible production token environment"
  text="Hermes staging gateway smoke from $(hostname -f 2>/dev/null || hostname) at $(date -Is)"
  curl -fsS -X POST "https://api.telegram.org/bot${STAGING_TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${STAGING_TELEGRAM_CHAT_ID}" \
    -d "message_thread_id=${STAGING_TELEGRAM_THREAD_ID:-}" \
    --data-urlencode "text=${text}" >/dev/null
  echo "staging Telegram smoke sent to staging chat"
else
  echo "Telegram send skipped; pass --send-staging-telegram with STAGING_TELEGRAM_* only after staging bot/chat approval."
fi

echo "gateway smoke complete"
