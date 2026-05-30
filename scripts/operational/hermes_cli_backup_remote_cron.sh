#!/usr/bin/env bash
set -euo pipefail
LOG_DIR="$HOME/.hermes/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/hermes-cli-importable-backup.log"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT="$HOME/.hermes/scripts/hermes_cli_backup_remote.py"
REMOTE="${HERMES_BACKUP_REMOTE:-root@proxmox01}"
REMOTE_DIR="${HERMES_CLI_BACKUP_REMOTE_DIR:-/mnt/agent-vault-backups/hermes-backups/hermes-vm/importable}"
MIN_AVAIL_GB="${HERMES_BACKUP_MIN_AVAIL_GB:-20}"
KEEP="${HERMES_CLI_BACKUP_KEEP:-7}"

exec 9>"$HOME/.hermes/hermes-cli-importable-backup.lock"
if ! flock -n 9; then
  echo "Hermes CLI importable backup skipped: previous backup still running at $TS"
  exit 2
fi

TMP="$(mktemp)"
if python3 "$SCRIPT" \
  --remote "$REMOTE" \
  --remote-dir "$REMOTE_DIR" \
  --min-avail-gb "$MIN_AVAIL_GB" \
  --keep "$KEEP" \
  --label scheduled >"$TMP" 2>&1; then
  {
    echo "[$TS] OK"
    cat "$TMP"
    echo
  } >> "$LOG"
  rm -f "$TMP"
  exit 0
else
  rc=$?
  {
    echo "[$TS] FAILED rc=$rc"
    cat "$TMP"
    echo
  } >> "$LOG"
  echo "Hermes CLI importable backup FAILED at $TS (exit $rc)"
  cat "$TMP"
  rm -f "$TMP"
  exit "$rc"
fi
