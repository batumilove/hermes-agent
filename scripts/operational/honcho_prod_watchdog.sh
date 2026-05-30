#!/usr/bin/env bash
set -euo pipefail
OUT=$(/home/ubuntu/.hermes/scripts/honcho_prod_smoke.py 2>&1) || {
  printf 'Honcho production smoke failed at %s UTC\n\n%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$OUT"
  exit 0
}
# Silence on success
exit 0
