#!/usr/bin/env python3
"""Ping self-hosted Healthchecks checks without printing capability URLs."""
from __future__ import annotations
import os, subprocess, sys
from pathlib import Path
from urllib.parse import urlparse

ENV_PATH = Path('/home/ubuntu/.hermes/secrets/homelab-healthchecks.env')
KEYS = {
    'daily-infrastructure-check': 'HOMELAB_HC_DAILY_URL',
    'weekly-rackpeek-inventory-refresh': 'HOMELAB_HC_WEEKLY_RACKPEEK_INVENTORY_REFRESH_URL',
    'nightly-k3s-cluster-health-audit': 'HOMELAB_HC_NIGHTLY_K3S_CLUSTER_HEALTH_AUDIT_URL',
}

def load_env(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(errors='ignore').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out

def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in KEYS:
        print('usage: homelab_healthcheck_ping.py <check-name> [start|success|fail]', file=sys.stderr)
        print('known: ' + ', '.join(sorted(KEYS)), file=sys.stderr)
        return 2
    name = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else 'success'
    if mode not in {'start', 'success', 'fail'}:
        print('mode must be start, success, or fail', file=sys.stderr)
        return 2
    env = load_env(ENV_PATH)
    url = env.get(KEYS[name]) or os.environ.get(KEYS[name])
    if not url:
        print(f'healthcheck_ping name={name} mode={mode} status=missing-url')
        return 1
    suffix = {'start': '/start', 'success': '', 'fail': '/fail'}[mode]
    target = url.rstrip('/') + suffix
    curl_cmd = ['curl', '-fsS', '--max-time', '10', '-o', '/dev/null']
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith('.batumi.works'):
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        ingress_ip = os.environ.get('HOMELAB_INGRESS_RESOLVE_IP', '100.102.52.45')
        curl_cmd += ['--resolve', f'{parsed.hostname}:{port}:{ingress_ip}']
    curl_cmd.append(target)
    try:
        subprocess.run(curl_cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f'healthcheck_ping name={name} mode={mode} status=failed exit={exc.returncode}')
        return 1
    print(f'healthcheck_ping name={name} mode={mode} status=ok')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
