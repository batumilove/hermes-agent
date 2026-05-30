#!/usr/bin/env python3
"""Report-only Agent Vault heartbeat for Hermes cron.

Checks Agent Vault LXC 220 on proxmox01 without printing secret values.
No paid API calls. It verifies the broker wiring by:
- checking wrapper/env shape locally,
- checking service/listeners/health/DNS in the LXC,
- opening an authenticated CONNECT to the MITM proxy and expecting 200,
- verifying recent Agent Vault request-log evidence exists for Deepgram,
- checking the Proxmox-hosted backup timer and latest backup freshness/checksum.
"""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import ssl
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

PROXMOX_HOST = os.environ.get("AGENT_VAULT_PROXMOX_HOST", "proxmox01")
LXC_ID = os.environ.get("AGENT_VAULT_LXC_ID", "220")
ENV_FILE = Path.home() / ".config" / "agent-vault" / "hermes-vm-agent.env"
AV_RUN = Path.home() / ".local" / "bin" / "av-run"
HERMES_AV = Path.home() / ".local" / "bin" / "hermes-av"
BACKUP_MAX_AGE_HOURS = int(os.environ.get("AGENT_VAULT_BACKUP_MAX_AGE_HOURS", "36"))

SECRET_PATTERNS = [
    re.compile(r"av_agt_[A-Za-z0-9._=-]+"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*[^\s]+"),
]


def run(cmd: list[str], *, timeout: int = 30, input_text: str | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout


def ssh_host(script: str, *, timeout: int = 45) -> tuple[int, str]:
    return run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"root@{PROXMOX_HOST}", "bash", "-s"],
        timeout=timeout,
        input_text=script,
    )


def ssh_lxc(script: str, *, timeout: int = 45) -> tuple[int, str]:
    # Feed the script over stdin. `pct exec ... bash -lc <script>` is brittle
    # across ssh/pct argument boundaries and can degrade to `bash -c` with an
    # empty argument.
    return run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"root@{PROXMOX_HOST}", "pct", "exec", LXC_ID, "--", "bash", "-s"],
        timeout=timeout,
        input_text=script,
    )


def parse_env_keys() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    for raw in ENV_FILE.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def assert_no_secret_output(text: str) -> None:
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            raise RuntimeError(f"heartbeat output failed secret hygiene check: {pat.pattern}")


def proxy_connect_smoke(env: dict[str, str]) -> tuple[bool, str]:
    """Open authenticated CONNECT to Agent Vault proxy without sending an upstream request.

    This proves: token parses, vault hint resolves, agent has vault access, proxy listener
    can mint/serve CONNECT. It does not complete TLS to the upstream or hit Deepgram.
    """
    proxy = env.get("HTTPS_PROXY", "")
    ca_file = env.get("SSL_CERT_FILE", "")
    u = urlsplit(proxy)
    if not (u.scheme == "https" and u.hostname and u.port and u.username and u.password):
        return False, "proxy URL shape invalid"
    if not ca_file or not Path(ca_file).exists():
        return False, "proxy CA file missing"
    try:
        ctx = ssl.create_default_context(cafile=ca_file)
        with socket.create_connection((u.hostname, u.port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=u.hostname) as tls_sock:
                auth = base64.b64encode(f"{u.username}:{u.password}".encode()).decode()
                request = (
                    "CONNECT api.deepgram.com:443 HTTP/1.1\r\n"
                    "Host: api.deepgram.com:443\r\n"
                    f"Proxy-Authorization: Basic {auth}\r\n"
                    "User-Agent: av-heartbeat\r\n"
                    "\r\n"
                )
                tls_sock.sendall(request.encode())
                response = tls_sock.recv(1024).decode(errors="ignore")
        first = response.replace("\r", "").split("\n", 1)[0]
        if first == "HTTP/1.1 200 Connection Established":
            return True, "CONNECT accepted"
        if "429" in first:
            return False, "CONNECT rate-limited"
        if "404" in first:
            return False, "CONNECT vault lookup failed"
        if "407" in first:
            return False, "CONNECT auth failed"
        return False, f"CONNECT unexpected status: {first}"
    except Exception as exc:
        return False, f"CONNECT probe error: {type(exc).__name__}"


def main() -> int:
    failures: list[str] = []
    notes: list[str] = []

    env = parse_env_keys()
    required_keys = {
        "AGENT_VAULT_ADDR",
        "AGENT_VAULT_SESSION_TOKEN",
        "AGENT_VAULT_AGENT_TOKEN",
        "AGENT_VAULT_VAULT",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "SSL_CERT_FILE",
    }
    missing = sorted(k for k in required_keys if not env.get(k))
    if missing:
        failures.append("missing env keys: " + ", ".join(missing))
    else:
        notes.append("agent env keys present")

    for path, label in [(AV_RUN, "av-run"), (HERMES_AV, "hermes-av")]:
        if path.exists() and os.access(path, os.X_OK):
            notes.append(f"{label} executable present")
        else:
            failures.append(f"{label} executable missing/not executable")

    proxy_hint_ok = False
    for key in ("HTTPS_PROXY", "HTTP_PROXY"):
        value = env.get(key, "")
        if ":default@" not in value:
            failures.append(f"{key} vault hint is not default")
        else:
            proxy_hint_ok = True
    if proxy_hint_ok:
        notes.append("proxy vault hint is default")

    lxc_script = r'''
set -euo pipefail
status=$(systemctl is-active agent-vault || true)
enabled=$(systemctl is-enabled agent-vault || true)
listeners=$(ss -ltnp | grep -E ':(14321|14322)' | wc -l | tr -d ' ')
health=$(curl -sS -m 5 http://127.0.0.1:14321/health || true)
dg_dns=$(getent hosts api.deepgram.com >/dev/null && echo ok || echo fail)
host_dns=$(getent hosts agent-vault.batumi.works >/dev/null && echo ok || echo fail)
version=$(/opt/agent-vault/bin/agent-vault --version 2>&1 | tr '\n' ' ')
# Recent successful brokered Deepgram evidence from Agent Vault's own request log.
# This is local SQLite only; no paid/API call.
DB=/opt/agent-vault/data/.agent-vault/agent-vault.db
recent_dg_count=0
if [ -r "$DB" ]; then
  recent_dg_count=$(sqlite3 "$DB" "select count(*) from request_logs where host='api.deepgram.com:443' and matched_service='api.deepgram.com' and credential_keys like '%DEEPGRAM_API_KEY%' and status between 200 and 299 and datetime(created_at) >= datetime('now','-7 days');" 2>/dev/null || echo 0)
fi
printf '{"service":"%s","enabled":"%s","listeners":%s,"health":%s,"deepgram_dns":"%s","hostname_dns":"%s","version":%s,"recent_dg_successes":%s}\n' \
  "$status" "$enabled" "$listeners" "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$health")" "$dg_dns" "$host_dns" "$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$version")" "$recent_dg_count"
'''
    code, out = ssh_lxc(lxc_script, timeout=45)
    if code != 0:
        failures.append("LXC service probe failed")
    else:
        try:
            data = json.loads(out.strip().splitlines()[-1])
            if data.get("service") != "active":
                failures.append(f"agent-vault service not active: {data.get('service')}")
            if data.get("listeners", 0) < 2:
                failures.append(f"expected listeners 14321/14322, got {data.get('listeners')}")
            if '"status":"ok"' not in data.get("health", ""):
                failures.append("/health did not return ok")
            if data.get("deepgram_dns") != "ok":
                failures.append("LXC cannot resolve api.deepgram.com")
            if data.get("hostname_dns") != "ok":
                notes.append("warning: LXC cannot resolve agent-vault.batumi.works yet")
            recent = int(data.get("recent_dg_successes") or 0)
            if recent <= 0:
                failures.append("no recent successful brokered Deepgram evidence in Agent Vault request log")
            notes.append(f"agent-vault service active; listeners={data.get('listeners')}; deepgram_dns={data.get('deepgram_dns')}")
            notes.append(f"recent brokered Deepgram successes in log: {recent}")
        except Exception as exc:
            failures.append(f"could not parse LXC probe output: {exc}")

    backup_script = f'''
set -euo pipefail
MP=/mnt/agent-vault-backups
DEST="$MP/agent-vault"
mounted_here=0
if ! mountpoint -q "$MP"; then
  mount -t nfs -o vers=4,soft,timeo=50,retrans=2 192.168.100.210:/mnt/pve/backup-hdd/longhorn-backups "$MP"
  mounted_here=1
fi
cleanup() {{ [ "$mounted_here" = 1 ] && umount "$MP" || true; }}
trap cleanup EXIT
service_enabled=$(systemctl is-enabled agent-vault-hdd-backup.timer || true)
service_active=$(systemctl is-active agent-vault-hdd-backup.timer || true)
latest=$(find "$DEST" -maxdepth 1 -type f -name 'agent-vault-lxc220-*.tar.gz' -printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1 | awk '{{print $2}}')
count=$(find "$DEST" -maxdepth 1 -type f -name 'agent-vault-lxc220-*.tar.gz' 2>/dev/null | wc -l | tr -d ' ')
fstype=$(stat -f -c %T "$MP" 2>/dev/null || echo unknown)
if [ -n "$latest" ]; then
  age_hours=$(( ($(date +%s) - $(stat -c %Y "$latest")) / 3600 ))
  base=${{latest%.tar.gz}}
  perms=$(stat -c '%A:%U:%G' "$latest")
  checksum=fail
  if [ -f "$latest.sha256" ]; then
    (cd "$DEST" && sha256sum -c "$(basename "$latest").sha256" >/dev/null 2>&1) && checksum=ok
  fi
  manifest=$([ -f "$base.manifest.json" ] && echo ok || echo missing)
else
  age_hours=-1
  perms=missing
  checksum=missing
  manifest=missing
fi
printf '{{"timer_enabled":"%s","timer_active":"%s","fstype":"%s","count":%s,"age_hours":%s,"checksum":"%s","manifest":"%s","perms":"%s","max_age_hours":%s}}\n' \
  "$service_enabled" "$service_active" "$fstype" "$count" "$age_hours" "$checksum" "$manifest" "$perms" "{BACKUP_MAX_AGE_HOURS}"
'''
    code, out = ssh_host(backup_script, timeout=60)
    if code != 0:
        failures.append("backup status probe failed")
    else:
        try:
            b = json.loads(out.strip().splitlines()[-1])
            if b.get("timer_enabled") != "enabled" or b.get("timer_active") != "active":
                failures.append(f"backup timer not enabled/active: {b.get('timer_enabled')}/{b.get('timer_active')}")
            if b.get("fstype") not in {"nfs", "nfs4"}:
                failures.append(f"backup mount is not NFS: {b.get('fstype')}")
            if int(b.get("count") or 0) < 1:
                failures.append("no Agent Vault backup archives found")
            raw_age = b.get("age_hours")
            age = int(raw_age) if raw_age is not None else -1
            if age < 0 or age > BACKUP_MAX_AGE_HOURS:
                failures.append(f"latest Agent Vault backup is stale/missing: age_hours={age}")
            if b.get("checksum") != "ok":
                failures.append("latest Agent Vault backup checksum failed/missing")
            if b.get("manifest") != "ok":
                failures.append("latest Agent Vault backup manifest missing")
            if b.get("perms") != "-rw-------:root:root":
                failures.append(f"latest Agent Vault backup permissions unexpected: {b.get('perms')}")
            notes.append(f"backup timer active; latest_age_hours={age}; archives={b.get('count')}; checksum={b.get('checksum')}")
        except Exception as exc:
            failures.append(f"could not parse backup probe output: {exc}")

    if not missing and AV_RUN.exists():
        ok, msg = proxy_connect_smoke(env)
        if ok:
            notes.append("broker CONNECT auth/vault probe ok; no upstream API call")
        else:
            failures.append(msg)

    status = "OK" if not failures else "FAIL"
    lines = [f"Agent Vault heartbeat: {status}"]
    if notes:
        lines.append("Checks:")
        lines.extend(f"- {n}" for n in notes)
    if failures:
        lines.append("Failures:")
        lines.extend(f"- {f}" for f in failures)

    final = "\n".join(lines)
    assert_no_secret_output(final)
    print(final)
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
