#!/usr/bin/env python3
"""
Honcho Deriver Prometheus Textfile Collector Exporter

Collects metrics from the Honcho deriver container and writes them in
Prometheus textfile format so node_exporter can pick them up.

Scrape config hint:
  Add the following to your Prometheus scrape config for node_exporter:
    scrape_configs:
      - job_name: 'node'
        static_configs:
          - targets: ['localhost:9100']
        metric_relabel_configs:  # optional, if you want to prefix
          - source_labels: [__name__]
            regex: 'honcho_deriver_.*'
            action: keep

  Or simply ensure node_exporter is started with:
    --collector.textfile.directory=/tmp
  (or /var/lib/prometheus/node-exporter-textfile-directory)
  and the .prom file will be picked up automatically.

Metrics exposed (all gauges unless noted):
  honcho_deriver_queue_completed         - completed work units
  honcho_deriver_queue_pending           - pending work units
  honcho_deriver_queue_in_progress       - in-progress work units
  honcho_deriver_queue_total             - total work units
  honcho_deriver_queue_completed_delta   - increase in completed since last run
  honcho_deriver_errors_total            - ERROR lines in last 5min of docker logs
  honcho_deriver_warnings_total          - WARNING lines in last 5min of docker logs
  honcho_deriver_container_running       - 1 if container is running, 0 otherwise
  honcho_deriver_container_restarts      - container restart count
  honcho_deriver_stalled                 - 1 if pending>0 AND completed_delta==0
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────────────

HONCHO_HOST = "100.67.206.76"
SSH_USER = "ubuntu"
SSH_OPTIONS = "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -o BatchMode=yes"
QUEUE_URL = "http://127.0.0.1:18000/v3/workspaces/hermes/queue/status"
DERIVER_CONTAINER = "honcho-deriver-1"

STATE_DIR = Path.home() / ".cache" / "honcho-deriver-monitor"
STATE_FILE = STATE_DIR / "status.json"
OUTPUT_FILE = Path("/tmp/honcho_deriver.prom")

NAMESPACE = "honcho_deriver"
TIMESTAMP = int(time.time())

LOG_LOOKBACK = "5m"  # docker logs --since


# ── Helpers ──────────────────────────────────────────────────────────────────

def ssh_cmd(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a command on the remote Honcho host via SSH. Returns (rc, stdout, stderr)."""
    full_cmd = f"ssh {SSH_OPTIONS} {SSH_USER}@{HONCHO_HOST} {cmd}"
    try:
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "SSH command timed out"
    except Exception as e:
        return -1, "", str(e)


def load_state() -> dict:
    """Load previous state from disk."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    """Persist state to disk."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


# ── Metric collectors ────────────────────────────────────────────────────────

def collect_queue_status() -> dict:
    """Fetch queue status from the Honcho API via local SSH forward.
    
    The forward is on THIS host (Hermes VM): 127.0.0.1:18000 -> Honcho VM:8000
    So we curl locally, not through SSH.
    """
    try:
        proc = subprocess.run(
            ["curl", "-sf", "--max-time", "10",
             "http://127.0.0.1:18000/v3/workspaces/hermes/queue/status"],
            capture_output=True, text=True, timeout=15
        )
        if proc.returncode != 0:
            print(f"WARN: queue status fetch failed: rc={proc.returncode} stderr={proc.stderr}", file=sys.stderr)
            return {}
        return json.loads(proc.stdout)
    except (json.JSONDecodeError, subprocess.TimeoutExpired, Exception) as e:
        print(f"WARN: queue status failed: {e}", file=sys.stderr)
        return {}


def collect_docker_logs_count(level: str = "ERROR") -> int:
    """Count lines with `level` in the last LOG_LOOKBACK of docker logs."""
    # Use grep -c to count matching lines; pipe through head for safety
    rc, stdout, stderr = ssh_cmd(
        f"docker logs --since {LOG_LOOKBACK} {DERIVER_CONTAINER} 2>&1 | "
        f"grep -c '{level}'",
        timeout=60,
    )
    if rc != 0 and rc != 1:
        # rc=1 from grep means 0 matches (acceptable), other errors are real
        if rc == 1:
            return 0
        print(f"WARN: docker logs grep for {level} failed: rc={rc} stderr={stderr}", file=sys.stderr)
        return 0
    try:
        return int(stdout.split("\n")[-1])
    except (ValueError, IndexError):
        return 0


def collect_container_running() -> int:
    """Check if the deriver container is running. Returns 1 or 0."""
    rc, stdout, stderr = ssh_cmd(
        f"docker inspect -f '{{{{.State.Running}}}}' {DERIVER_CONTAINER}"
    )
    if rc == 0 and stdout.strip().lower() == "true":
        return 1
    return 0


def collect_container_restarts() -> int:
    """Get the restart count for the deriver container."""
    rc, stdout, stderr = ssh_cmd(
        f"docker inspect -f '{{{{.RestartCount}}}}' {DERIVER_CONTAINER}"
    )
    if rc == 0:
        try:
            return int(stdout.strip())
        except ValueError:
            pass
    return 0


# ── Prometheus textfile writer ───────────────────────────────────────────────

def write_prom(metrics: dict[str, float]) -> None:
    """Write metrics in Prometheus textfile exposition format."""
    lines = []
    lines.append(f"# Generated by honcho_deriver_exporter.py at {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"# TIMESTAMP {TIMESTAMP}")
    lines.append("")

    for name, value in sorted(metrics.items()):
        lines.append(f"# HELP {name} Honcho deriver metric: {name}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value}")
        lines.append("")

    OUTPUT_FILE.write_text("\n".join(lines) + "\n")
    # Prometheus textfile collector requires the .prom suffix and atomic read
    # Write a temp file then rename for atomicity
    tmp_file = OUTPUT_FILE.with_suffix(".prom.tmp")
    tmp_file.write_text("\n".join(lines) + "\n")
    tmp_file.rename(OUTPUT_FILE)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    state = load_state()
    prev_completed = state.get("completed", 0)

    metrics: dict[str, float] = {}

    # 1. Queue status
    queue = collect_queue_status()
    completed = queue.get("completed_work_units", 0)
    pending = queue.get("pending_work_units", 0)
    in_progress = queue.get("in_progress_work_units", 0)
    total = queue.get("total_work_units", 0)

    completed_delta = max(0, completed - prev_completed)

    metrics[f"{NAMESPACE}_queue_completed"] = completed
    metrics[f"{NAMESPACE}_queue_pending"] = pending
    metrics[f"{NAMESPACE}_queue_in_progress"] = in_progress
    metrics[f"{NAMESPACE}_queue_total"] = total
    metrics[f"{NAMESPACE}_queue_completed_delta"] = completed_delta

    # 2. Docker logs (ERROR / WARNING counts)
    metrics[f"{NAMESPACE}_errors_total"] = collect_docker_logs_count("ERROR")
    metrics[f"{NAMESPACE}_warnings_total"] = collect_docker_logs_count("WARNING")

    # 3. Container health
    running = collect_container_running()
    metrics[f"{NAMESPACE}_container_running"] = running
    metrics[f"{NAMESPACE}_container_restarts"] = collect_container_restarts()

    # 4. Stalled detection: pending > 0 but no progress
    stalled = 1 if (pending > 0 and completed_delta == 0) else 0
    metrics[f"{NAMESPACE}_stalled"] = stalled

    # Write output
    write_prom(metrics)

    # Persist state
    new_state = {
        "completed": completed,
        "pending": pending,
        "total": total,
    }
    save_state(new_state)

    print(f"OK: wrote {len(metrics)} metrics to {OUTPUT_FILE}")
    for k, v in sorted(metrics.items()):
        print(f"  {k} {v}")


if __name__ == "__main__":
    main()
