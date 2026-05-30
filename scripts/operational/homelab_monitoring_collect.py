#!/usr/bin/env python3
"""Collect homelab monitoring state from Git-reviewed policy and live tools.
Outputs JSON for Hermes cron and humans. No secrets."""
from __future__ import annotations

import json, os, subprocess, sys, time, urllib.request, ssl, urllib.parse
from pathlib import Path

try:
    import yaml
except Exception:
    yaml = None

POLICY = Path(os.environ.get("HOMELAB_MONITORING_POLICY", "/home/ubuntu/ops-monitoring/policy/homelab-monitoring-policy.yaml"))
STATE = Path(os.environ.get("HOMELAB_MONITORING_STATE", "/home/ubuntu/.hermes/state/homelab-monitoring-last.json"))
HEALTHCHECKS_ENV = Path(os.environ.get("HOMELAB_HEALTHCHECKS_ENV", "/home/ubuntu/.hermes/secrets/homelab-healthchecks.env"))


def load_env_file(path: Path):
    vals = {}
    if not path.exists():
        return vals
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        vals[k.strip()] = v.strip()
    return vals


def ping_healthcheck(url: str | None, suffix: str = ""):
    if not url:
        return False
    target = url.rstrip("/") + suffix
    cmd = ["curl", "-fsS", "--max-time", "10", "-o", "/dev/null"]
    parsed = urllib.parse.urlparse(target)
    if parsed.hostname == "healthchecks.batumi.works":
        port = parsed.port or 443
        ingress_ip = os.environ.get("HOMELAB_INGRESS_RESOLVE_IP", "100.102.52.45")
        cmd += ["--resolve", f"{parsed.hostname}:{port}:{ingress_ip}"]
    cmd.append(target)
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=12).returncode == 0
    except Exception:
        return False


def run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        return {"cmd": cmd, "rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}
    except Exception as e:
        return {"cmd": cmd, "rc": 999, "stdout": "", "stderr": str(e)}


def load_policy():
    if not POLICY.exists():
        raise SystemExit(f"policy missing: {POLICY}")
    if yaml is None:
        raise SystemExit("PyYAML unavailable; cannot parse policy")
    return yaml.safe_load(POLICY.read_text()) or {}


def http_check(svc):
    url = svc.get("url")
    expected = int(svc.get("expected_status", 200))
    t0 = time.time()
    resolve_ip = svc.get("resolve_ip")
    if resolve_ip:
        u = urllib.parse.urlparse(url)
        port = u.port or (443 if u.scheme == "https" else 80)
        # Do not use -f: expected statuses may intentionally be 3xx/4xx
        # for auth-gated endpoints such as Dex or Harbor /v2/.
        curl = ["curl", "-sS", "-o", "/tmp/hermes-monitor-body", "-w", "%{http_code} %{time_total}", "--max-time", "12", "--resolve", f"{u.hostname}:{port}:{resolve_ip}", url]
        r = run(curl, timeout=15)
        parts = r["stdout"].strip().split()
        if parts and parts[0].isdigit():
            status = int(parts[0])
            latency_ms = int(float(parts[1]) * 1000) if len(parts) > 1 else int((time.time()-t0)*1000)
            result = {"name": svc.get("name"), "url": url, "ok": status == expected, "status": status, "expected_status": expected, "latency_ms": latency_ms, "resolve_ip": resolve_ip}
            # curl still emits an http_code of 000 on TLS/DNS/connect failures. Preserve
            # stderr so the report can distinguish a backend outage from certificate
            # expiry or name resolution issues instead of only saying "status 0".
            if status == 0 and r.get("stderr"):
                result["error"] = r["stderr"][:1000]
            return result
        return {"name": svc.get("name"), "url": url, "ok": False, "error": r["stderr"][:1000] or r["stdout"][:1000], "expected_status": expected, "latency_ms": int((time.time()-t0)*1000), "resolve_ip": resolve_ip}
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(url, method=svc.get("method", "GET"), headers={"User-Agent": "hermes-homelab-monitor/1"})
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            status = int(r.status)
            body = r.read(256)
        return {"name": svc.get("name"), "url": url, "ok": status == expected, "status": status, "expected_status": expected, "latency_ms": int((time.time()-t0)*1000), "bytes_sampled": len(body)}
    except Exception as e:
        return {"name": svc.get("name"), "url": url, "ok": False, "error": type(e).__name__ + ": " + str(e), "expected_status": expected, "latency_ms": int((time.time()-t0)*1000)}


def main():
    hc_url = load_env_file(HEALTHCHECKS_ENV).get("HOMELAB_HC_DAILY_URL")
    ping_healthcheck(hc_url, "/start")
    policy = load_policy()
    out = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "policy_path": str(POLICY), "hosts": [], "services": [], "k3s_nodes": [], "tailscale": {}, "infisical_audit": {}, "changes": []}

    ts = run(["tailscale", "status", "--json"], timeout=20)
    if ts["rc"] == 0:
        data = json.loads(ts["stdout"])
        peers = data.get("Peer", {}) or {}
        observed = {}
        for _id, p in peers.items():
            name = (p.get("HostName") or p.get("DNSName") or "").rstrip(".")
            observed[name] = {"online": bool(p.get("Online")), "tailscale_ips": p.get("TailscaleIPs", []), "dns": p.get("DNSName")}
        self_name = (data.get("Self", {}).get("HostName") or "").rstrip(".")
        observed[self_name] = {"online": True, "tailscale_ips": data.get("Self", {}).get("TailscaleIPs", []), "dns": data.get("Self", {}).get("DNSName")}
        out["tailscale"] = {"ok": True, "self": data.get("Self", {}).get("DNSName"), "warning": data.get("Health", [])}
    else:
        observed = {}
        out["tailscale"] = {"ok": False, "error": ts["stderr"][:1000]}

    for h in policy.get("hosts", []):
        name = h.get("name")
        obs = observed.get(name, {})
        expected_ips = set(h.get("expected_tailscale_ips") or [])
        actual_ips = set(obs.get("tailscale_ips") or [])
        ok = bool(obs.get("online", False))
        if expected_ips:
            ok = ok and bool(expected_ips & actual_ips)
        out["hosts"].append({"name": name, "role": h.get("role"), "ok": ok, "online": obs.get("online"), "expected_tailscale_ips": sorted(expected_ips), "actual_tailscale_ips": sorted(actual_ips)})

    kg = run(["kubectl", "get", "nodes", "-o", "json"], timeout=30)
    if kg["rc"] == 0:
        nodes = json.loads(kg["stdout"])
        for n in nodes.get("items", []):
            ready = False
            for c in n.get("status", {}).get("conditions", []):
                if c.get("type") == "Ready":
                    ready = c.get("status") == "True"
            out["k3s_nodes"].append({"name": n["metadata"]["name"], "ready": ready, "version": n.get("status", {}).get("nodeInfo", {}).get("kubeletVersion")})

    out["services"] = [http_check(s) for s in policy.get("services", [])]

    audit_policy = policy.get("infisical_audit", {}) or {}
    audit_cmd = [
        sys.executable,
        str(Path.home() / ".hermes/scripts/infisical_audit_monitor.py"),
        "--state-file",
        str(Path.home() / ".hermes/state/infisical-audit-monitor-last.json"),
        "--metrics-file",
        str(Path.home() / ".hermes/state/prometheus/infisical_audit.prom"),
        "--max-age-seconds",
        str(audit_policy.get("max_age_seconds", 86400)),
    ]
    if audit_policy.get("expected_paths"):
        audit_cmd += ["--expected-paths", ",".join(audit_policy.get("expected_paths") or [])]
    if audit_policy.get("expected_wrappers"):
        audit_cmd += ["--expected-wrappers", ",".join(audit_policy.get("expected_wrappers") or [])]
    audit = run(audit_cmd, timeout=20)
    if audit["rc"] == 0 and audit.get("stdout"):
        try:
            out["infisical_audit"] = json.loads(audit["stdout"])
        except Exception as e:
            out["infisical_audit"] = {"ok": False, "error": "parse_monitor_output_failed: " + str(e)}
    else:
        out["infisical_audit"] = {"ok": False, "error": (audit.get("stderr") or audit.get("stdout") or "infisical audit monitor failed")[:1000]}

    STATE.parent.mkdir(parents=True, exist_ok=True)
    if STATE.exists():
        try:
            prev = json.loads(STATE.read_text())
            prev_hosts = {h["name"]: h.get("ok") for h in prev.get("hosts", [])}
            for h in out["hosts"]:
                if h["name"] in prev_hosts and prev_hosts[h["name"]] != h.get("ok"):
                    out["changes"].append({"type": "host_ok_changed", "name": h["name"], "from": prev_hosts[h["name"]], "to": h.get("ok")})
            prev_svcs = {s["name"]: s.get("ok") for s in prev.get("services", [])}
            for s in out["services"]:
                if s["name"] in prev_svcs and prev_svcs[s["name"]] != s.get("ok"):
                    out["changes"].append({"type": "service_ok_changed", "name": s["name"], "from": prev_svcs[s["name"]], "to": s.get("ok")})
        except Exception:
            pass
    STATE.write_text(json.dumps(out, indent=2, sort_keys=True))
    print(json.dumps(out, indent=2, sort_keys=True))
    # The collector reports unhealthy items in JSON; do not fail the wrapper just because
    # infrastructure has something to report. Cron/Hermes should summarize the state.
    ping_healthcheck(hc_url)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
