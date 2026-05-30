#!/usr/bin/env bash
# Olah HF Mirror Proxy Health Monitor
# Runs healthcheck via SSH and only outputs when something is wrong.
# Silent (no stdout) = all healthy.

set -euo pipefail

# SSH into Proxmox host, run healthcheck inside CT 313
OUTPUT=$(ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no -o BatchMode=yes \
    root@192.168.100.210 'pct exec 313 -- /opt/olah/bin/olah-healthcheck' 2>&1)
SSH_EXIT=$?

if [ $SSH_EXIT -ne 0 ]; then
    echo "🚨 OLAH ALERT: SSH/healthcheck command failed (exit $SSH_EXIT)"
    echo "Output: $OUTPUT"
    exit 0
fi

ALERT=""

# Check for CRITICAL status
if echo "$OUTPUT" | grep -qi "CRITICAL"; then
    ALERT="${ALERT}🚨 CRITICAL status detected\n"
fi

# Check for inactive/down service
if echo "$OUTPUT" | grep -qiE 'olah=(inactive|down|stopped|failed)'; then
    ALERT="${ALERT}🚨 Olah service is NOT active\n"
fi

# Check disk usage > 85%
# Match patterns like disk=87% or disk=92.3%
DISK_PCT=$(echo "$OUTPUT" | grep -oP 'disk=\K[0-9]+(\.[0-9]+)?' | head -1)
if [ -n "$DISK_PCT" ]; then
    # Compare as integer (truncate decimal)
    DISK_INT=${DISK_PCT%%.*}
    if [ "$DISK_INT" -gt 85 ] 2>/dev/null; then
        ALERT="${ALERT}⚠️ Disk usage high: ${DISK_PCT}%\n"
    fi
fi

# Check for any FAIL/ERROR keywords
if echo "$OUTPUT" | grep -qiE '(FAIL|ERROR|timeout|unreachable)'; then
    ALERT="${ALERT}⚠️ Failure/error detected in output\n"
fi

# If we have alerts, output them
if [ -n "$ALERT" ]; then
    echo "🔴 Olah HF Mirror Proxy - Health Alert"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo -e "$ALERT"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Raw output:"
    echo "$OUTPUT"
fi

# No output = healthy (cron no_agent stays silent)
