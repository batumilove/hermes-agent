#!/usr/bin/env bash
set -euo pipefail

PATCH_BRANCH="${PATCH_BRANCH:-local/hermes-patch-stack-20260426}"
UPSTREAM_REF="${UPSTREAM_REF:-origin/main}"
TEST_CMD="${TEST_CMD:-python -m pytest tests/cron/test_cron_context_from.py tests/tools/test_approval.py tests/hermes_cli/test_update_bundled_scripts.py tests/tools/test_bundled_scripts_sync.py tests/scripts/test_local_workflow_scripts.py -q -o addopts=}"
RESTART_GATEWAY=1
DRY_RUN=0

usage() {
  cat <<EOF
Usage: scripts/local-update-with-patches.sh [--dry-run] [--no-restart] [--branch NAME] [--upstream REF]

Safely update this checkout's local Hermes patch stack:
  - fetch origin/upstream/myfork
  - switch to the local patch branch (${PATCH_BRANCH})
  - create backup/local-patches-before-update-<timestamp>
  - rebase on ${UPSTREAM_REF}
  - audit conflict markers and git status
  - run targeted patch-stack tests
  - restart hermes-gateway unless --no-restart is passed

This is a local workflow wrapper. It exists because generic 'hermes update'
can switch to main and reset local-only commits out of branch history.
EOF
}

run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+ %s' "$1"
    shift
    for arg in "$@"; do
      printf ' %s' "$arg"
    done
    printf '\n'
  else
    "$@"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-restart) RESTART_GATEWAY=0; shift ;;
    --branch) PATCH_BRANCH="$2"; shift 2 ;;
    --upstream) UPSTREAM_REF="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

cd "$(git rev-parse --show-toplevel)"

if [[ "$DRY_RUN" == "1" ]]; then
  cat <<EOF
# Dry run: local patch update workflow
PATCH_BRANCH=${PATCH_BRANCH}
UPSTREAM_REF=${UPSTREAM_REF}
backup/local-patches-before-update-$(date +%Y%m%d-%H%M%S)
EOF
fi

for remote in origin upstream myfork; do
  run git fetch "$remote" --prune
done
run git switch "$PATCH_BRANCH"
run git status --short --branch
run git branch "backup/local-patches-before-update-$(date +%Y%m%d-%H%M%S)" HEAD
run git rebase "$UPSTREAM_REF"
run git status --short --branch

if [[ "$DRY_RUN" == "1" ]]; then
  echo "+ git grep -n -E '^(<<<<<<<|>>>>>>>)' -- '*.py' '*.md' '*.yaml' '*.yml' '*.json' || true"
else
  git grep -n -E '^(<<<<<<<|>>>>>>>)' -- '*.py' '*.md' '*.yaml' '*.yml' '*.json' && {
    echo "Conflict markers remain; aborting." >&2
    exit 1
  } || true
fi

if [[ -d venv ]]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

if [[ "$DRY_RUN" == "1" ]]; then
  echo "+ ${TEST_CMD}"
else
  eval "$TEST_CMD"
fi

if [[ "$RESTART_GATEWAY" == "1" ]]; then
  run systemctl --user restart hermes-gateway
else
  echo "Skipping gateway restart (--no-restart)."
fi
