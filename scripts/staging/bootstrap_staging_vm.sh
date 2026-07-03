#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'USAGE'
Usage: HERMES_ENV=staging bootstrap_staging_vm.sh [--dry-run]

Idempotent bootstrap helper for a dedicated Hermes staging VM.
It refuses to run unless HERMES_ENV=staging and the host/profile inputs look staging-only.
USAGE
}

DRY_RUN=0
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

require_staging_guard() {
  if [[ "${HERMES_ENV:-}" != "staging" ]]; then
    echo "ERROR: refusing bootstrap: set HERMES_ENV=staging" >&2
    exit 10
  fi
  local host
  host="$(hostname -f 2>/dev/null || hostname)"
  if [[ "${ALLOW_NON_STAGING_HOSTNAME:-0}" != "1" && ! "$host" =~ (stage|staging|canary|hermes-staging) ]]; then
    echo "ERROR: hostname '$host' does not look staging. Set ALLOW_NON_STAGING_HOSTNAME=1 only after controller approval." >&2
    exit 11
  fi
}

run_shell() {
  echo "+ $*"
  if [[ "$DRY_RUN" == "0" ]]; then
    bash -lc "$*"
  fi
}

require_staging_guard

STAGING_USER="${HERMES_STAGING_USER:-hermes-staging}"
STAGING_HOME="${HERMES_STAGING_HOME:-/home/${STAGING_USER}/.hermes-staging}"
INSTALL_ROOT="${HERMES_STAGING_ROOT:-/opt/hermes-staging}"
REPO_URL="${HERMES_STAGING_REPO:-https://github.com/batumilove/hermes-agent.git}"
BRANCH="${HERMES_STAGING_BRANCH:-batumi/staging}"
ENV_DIR="${HERMES_STAGING_ENV_DIR:-/etc/hermes-staging}"
ENV_FILE="${HERMES_STAGING_ENV_FILE:-${ENV_DIR}/staging.env}"
CHECKOUT="${INSTALL_ROOT}/hermes-agent"

if [[ "$BRANCH" != "batumi/staging" ]]; then
  if [[ "$BRANCH" == "batumi/live" && "${HERMES_STAGING_ALLOW_LIVE_BRANCH:-0}" == "1" ]]; then
    echo "WARN: using batumi/live as staging checkout source because batumi/staging is unavailable; staging isolation must come from dedicated VM, staging HERMES_HOME, and staging-only profiles." >&2
  else
    echo "ERROR: refusing non-staging branch '$BRANCH'; expected batumi/staging, or set HERMES_STAGING_ALLOW_LIVE_BRANCH=1 with HERMES_STAGING_BRANCH=batumi/live after controller approval" >&2
    exit 12
  fi
fi
CURRENT_HOME="${HOME:-}"
if [[ "$STAGING_HOME" == "/home/ubuntu/.hermes" || -n "$CURRENT_HOME" && "$STAGING_HOME" == "$CURRENT_HOME/.hermes" ]]; then
  echo "ERROR: HERMES_STAGING_HOME resolves to a production/default Hermes home: $STAGING_HOME" >&2
  exit 13
fi
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN is set; refusing possible production Telegram token in bootstrap environment. Use staging-only credentials in STAGING_TELEGRAM_BOT_TOKEN after approval." >&2
  exit 16
fi

cat <<INFO
Hermes staging bootstrap
  dry_run:       $DRY_RUN
  host:          $(hostname -f 2>/dev/null || hostname)
  staging_user:  $STAGING_USER
  staging_home:  $STAGING_HOME
  install_root:  $INSTALL_ROOT
  checkout:      $CHECKOUT
  branch:        $BRANCH
  env_file:      $ENV_FILE
INFO

if command -v cloudflared >/dev/null 2>&1 || systemctl list-unit-files 'cloudflared*' 2>/dev/null | grep -q cloudflared; then
  echo "ERROR: cloudflared appears installed/configured. Staging VM must be Tailscale-only with no Cloudflare tunnel/public ingress." >&2
  exit 14
fi

if command -v tailscale >/dev/null 2>&1; then
  run_shell "tailscale status >/dev/null"
else
  echo "WARN: tailscale command not found; install/enroll Tailscale before treating the VM as ready." >&2
fi

if [[ "$(id -u)" -ne 0 && "$DRY_RUN" == "0" ]]; then
  echo "ERROR: bootstrap must run as root for package/user/directory setup. Use --dry-run for planning." >&2
  exit 15
fi

run_shell "install -d -m 0755 '$INSTALL_ROOT' '$ENV_DIR'"
if ! id "$STAGING_USER" >/dev/null 2>&1; then
  run_shell "useradd --create-home --shell /bin/bash '$STAGING_USER'"
fi
run_shell "chown '$STAGING_USER:$STAGING_USER' '$INSTALL_ROOT'"
run_shell "touch '$ENV_FILE' && chmod 0600 '$ENV_FILE'"

if command -v apt-get >/dev/null 2>&1; then
  run_shell "apt-get update"
  run_shell "DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates python3 python3-venv python3-pip build-essential shellcheck"
else
  echo "WARN: apt-get not found; install git/curl/python3/venv/build tools manually." >&2
fi

if [[ ! -d "$CHECKOUT/.git" ]]; then
  run_shell "sudo -u '$STAGING_USER' git clone --branch '$BRANCH' '$REPO_URL' '$CHECKOUT'"
else
  run_shell "cd '$CHECKOUT' && sudo -u '$STAGING_USER' git fetch --prune origin '$BRANCH' && sudo -u '$STAGING_USER' git checkout '$BRANCH' && sudo -u '$STAGING_USER' git pull --ff-only"
fi

run_shell "cd '$CHECKOUT' && sudo -u '$STAGING_USER' git status --short --branch"
run_shell "install -d -m 0755 '$CHECKOUT/scripts/staging'"
run_shell "install -m 0755 '$SCRIPT_DIR/staging_gateway_smoke.sh' '$CHECKOUT/scripts/staging/staging_gateway_smoke.sh'"
run_shell "install -m 0755 '$SCRIPT_DIR/staging_cron_smoke.sh' '$CHECKOUT/scripts/staging/staging_cron_smoke.sh'"
run_shell "install -m 0755 '$SCRIPT_DIR/bootstrap_staging_vm.sh' '$CHECKOUT/scripts/staging/bootstrap_staging_vm.sh'"
run_shell "sudo -u '$STAGING_USER' mkdir -p '$STAGING_HOME/profiles/skill-lab' '$STAGING_HOME/profiles/gateway-canary'"
run_shell "sudo -u '$STAGING_USER' bash -lc 'cd \"$CHECKOUT\" && python3 -m venv venv && venv/bin/python -m pip install -U pip && if [[ -f pyproject.toml ]]; then venv/bin/pip install -e .; fi'"

cat <<NEXT

Bootstrap finished/planned.
Next manual steps:
  1. Put staging-only credentials in $ENV_FILE if approved. Never copy production Telegram token or production .env.
  2. Bootstrap installs the staging helper scripts into $CHECKOUT/scripts/staging/ before smoke execution.
  3. Run: HERMES_ENV=staging HERMES_HOME=$STAGING_HOME HERMES_PROFILE=gateway-canary $CHECKOUT/scripts/staging/staging_gateway_smoke.sh
  4. Run: HERMES_ENV=staging HERMES_HOME=$STAGING_HOME HERMES_PROFILE=gateway-canary $CHECKOUT/scripts/staging/staging_cron_smoke.sh
  5. If backup is claimed, verify an off-host/offsite target and restore evidence.
NEXT
