#!/usr/bin/env bash
set -uo pipefail

# Conservative weekly cleanup for hermes-vm. Removes only rebuildable caches,
# stale temporary browser/download artifacts, old test workdirs, and old logs.
# Never touches durable Hermes sessions, state snapshots, repos, vaults, or .camofox profiles.

now_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
disk_line() { df -h / | awk 'NR==2 {printf "%s used=%s avail=%s pct=%s", $1,$3,$4,$5}'; }
bytes_dir() {
  if [ -e "$1" ]; then
    du -sb "$1" 2>/dev/null | awk 'BEGIN{s=0} {s+=$1} END{print s+0}'
  else
    echo 0
  fi
}
human_bytes() { numfmt --to=iec-i --suffix=B "$1" 2>/dev/null || echo "${1}B"; }

START_TS="$(now_utc)"
BEFORE="$(disk_line)"
REPORT=()
REPORT+=("Hermes VM weekly disk cleanup @ ${START_TS}")
REPORT+=("Before: ${BEFORE}")

freed=0
add_freed() {
  local label="$1" before="$2" after="$3" diff=0
  if [ "$before" -gt "$after" ]; then
    diff=$((before-after))
    freed=$((freed+diff))
    REPORT+=("- ${label}: freed $(human_bytes "$diff")")
  fi
}

remove_glob_older_than() {
  local parent="$1" name_glob="$2" min_age="$3" label="$4"
  [ -d "$parent" ] || return 0
  local before after
  before="$(bytes_dir "$parent")"
  # Print exact paths before removal for audit. Limit to one filesystem and depth 1.
  local victims_file
  victims_file="$(mktemp)"
  find "$parent" -xdev -mindepth 1 -maxdepth 1 -name "$name_glob" -mmin +"$min_age" -print0 >"$victims_file" 2>/dev/null || true
  local count
  count=$(tr -cd '\0' <"$victims_file" | wc -c | tr -d ' ')
  if [ "${count:-0}" -gt 0 ]; then
    xargs -0r rm -rf -- <"$victims_file"
    REPORT+=("- ${label}: removed ${count} stale item(s)")
  fi
  rm -f "$victims_file"
  after="$(bytes_dir "$parent")"
  add_freed "$label" "$before" "$after"
}

# Camofox/Camoufox-specific temp artifacts. Keep the installed engine in ~/.cache/camoufox
# and persistent profiles in ~/.camofox; remove only stale /tmp download/unpack dirs.
remove_glob_older_than /tmp 'camoufox-*' 360 'stale /tmp/camoufox-* dirs (>6h)'
remove_glob_older_than /tmp 'camofox-*' 360 'stale /tmp/camofox-* dirs (>6h)'

# Other known large, rebuildable Hermes/agent leftovers from prior ENOSPC incidents.
remove_glob_older_than /tmp 'gc-hermes-container*' 1440 'old gc-hermes-container temp dirs (>1d)'
remove_glob_older_than /tmp 'hermes-workspace-audit*' 1440 'old hermes workspace audit temp dirs (>1d)'
remove_glob_older_than /tmp 'ouroboros-audit*' 1440 'old ouroboros audit temp dirs (>1d)'
remove_glob_older_than /tmp 'libretto-inspect*' 1440 'old libretto inspect temp dirs (>1d)'
remove_glob_older_than /tmp 'openclaw-remotion*' 1440 'old openclaw remotion temp dirs (>1d)'
remove_glob_older_than /tmp 'wg-*-cache*' 1440 'old temporary npm cache dirs (>1d)'
remove_glob_older_than /tmp 'pytest-of-*' 10080 'old pytest temp dirs (>7d)'
remove_glob_older_than /tmp 'vault-*' 10080 'old vault merge temp dirs (>7d)'
remove_glob_older_than /tmp 'obsidian-*' 10080 'old obsidian temp dirs (>7d)'

# Truncate known runaway temporary logs only.
if [ -f /tmp/oh-my-opencode.log ]; then
  size=$(stat -c %s /tmp/oh-my-opencode.log 2>/dev/null || echo 0)
  if [ "$size" -gt $((50*1024*1024)) ]; then
    : > /tmp/oh-my-opencode.log
    freed=$((freed+size))
    REPORT+=("- /tmp/oh-my-opencode.log: truncated $(human_bytes "$size")")
  fi
fi

# Clean rebuildable package-manager caches. npm cache has previously consumed >1GB here.
if command -v npm >/dev/null 2>&1; then
  before="$(bytes_dir "$HOME/.npm/_cacache")"
  npm cache clean --force >/dev/null 2>&1 || npm cache verify >/dev/null 2>&1 || true
  after="$(bytes_dir "$HOME/.npm/_cacache")"
  add_freed "npm cache" "$before" "$after"
fi
if command -v apt-get >/dev/null 2>&1; then
  before="$(bytes_dir /var/cache/apt)"
  sudo -n apt-get clean >/dev/null 2>&1 || true
  after="$(bytes_dir /var/cache/apt)"
  add_freed "apt cache" "$before" "$after"
fi

# Journal cap: safe, preserves recent logs but prevents unbounded growth.
if command -v journalctl >/dev/null 2>&1; then
  sudo -n journalctl --vacuum-size=500M >/dev/null 2>&1 || journalctl --user --vacuum-size=200M >/dev/null 2>&1 || true
fi

# Verify Camofox remains healthy if configured/running.
CAMOFOX_HEALTH="not checked"
if systemctl --user is-active --quiet camofox-browser.service 2>/dev/null; then
  if curl -fsS --max-time 10 http://127.0.0.1:9377/health >/tmp/camofox-health-check.json 2>/dev/null; then
    if python3 - <<'PY' >/dev/null 2>&1
import json
j=json.load(open('/tmp/camofox-health-check.json'))
assert j.get('ok') is True and j.get('engine') == 'camoufox'
PY
    then
      CAMOFOX_HEALTH="ok"
    else
      CAMOFOX_HEALTH="unexpected health payload"
    fi
  else
    CAMOFOX_HEALTH="health endpoint failed"
  fi
fi
rm -f /tmp/camofox-health-check.json

AFTER="$(disk_line)"
REPORT+=("After: ${AFTER}")
REPORT+=("Total estimated freed: $(human_bytes "$freed")")
REPORT+=("Camofox health: ${CAMOFOX_HEALTH}")

# Emit a warning when root disk remains tight, even if cleanup succeeded.
pct=$(df / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
if [ "${pct:-0}" -ge 90 ]; then
  REPORT+=("WARNING: root filesystem still >=90% used; manual disk expansion/deeper audit recommended.")
fi

printf '%s\n' "${REPORT[@]}"
