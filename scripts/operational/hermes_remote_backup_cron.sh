#!/usr/bin/env bash
set -euo pipefail
LOG_DIR="$HOME/.hermes/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/hermes-remote-backup.log"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SCRIPT="$HOME/.hermes/scripts/hermes_remote_backup.py"
REMOTE="${HERMES_BACKUP_REMOTE:-root@proxmox01}"
REMOTE_DIR="${HERMES_BACKUP_REMOTE_DIR:-/mnt/agent-vault-backups/hermes-backups/hermes-vm}"
MIN_AVAIL_GB="${HERMES_BACKUP_MIN_AVAIL_GB:-20}"
KEEP_DAILY="${HERMES_BACKUP_KEEP_DAILY:-14}"
TIME_BUDGET="${HERMES_BACKUP_TIME_BUDGET_SECONDS:-105}"

# Keep one backup at a time. If a prior run is still active, report and exit non-zero
# so Hermes cron alerts instead of silently overlapping archives.
exec 9>"$HOME/.hermes/hermes-remote-backup.lock"
if ! flock -n 9; then
  echo "Hermes remote backup skipped: previous backup still running at $TS"
  exit 2
fi

TMP="$(mktemp)"
if python3 "$SCRIPT" \
  --remote "$REMOTE" \
  --remote-dir "$REMOTE_DIR" \
  --min-avail-gb "$MIN_AVAIL_GB" \
  --keep-daily "$KEEP_DAILY" \
  --label scheduled \
  --no-quick \
  --max-runtime-seconds "$TIME_BUDGET" \
  --skip-dir state-snapshots >"$TMP" 2>&1; then
  {
    echo "[$TS] OK"
    cat "$TMP"
    echo
  } >> "$LOG"
  rm -f "$TMP"
  # Quiet on success for no-agent Hermes cron.
  exit 0
else
  rc=$?
  {
    echo "[$TS] FAILED rc=$rc"
    cat "$TMP"
    echo
  } >> "$LOG"
  echo "Hermes remote backup FAILED at $TS (exit $rc)"
  cat "$TMP"
  rm -f "$TMP"
  exit "$rc"
fi
