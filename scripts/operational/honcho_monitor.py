#!/usr/bin/env python3
"""Honcho pipeline health monitor — cron every 15 min."""
import subprocess, json, time, re
from datetime import datetime

HONCHO = "ubuntu@100.67.206.76"
SPARK_GOAT_EMBED = "http://100.69.54.37:11435"
DERIVER_EXPORTER = "http://100.67.206.76:9101/metrics"

# Known host shortnames
HOST_MAP = {
    "100.69.54.37:8001": "spark-goat",
    "100.69.54.37:11435": "spark-goat",
    "100.110.104.77:8087": "mac-horse",
    "192.168.100.14:8088": "mac-horse",
    "openrouter.ai/api/v1": "openrouter",
}

def short_host(url):
    if not url: return "?"
    for long, short in HOST_MAP.items():
        if long in url: return short
    # Fallback: extract host:port
    m = re.search(r'://([^/]+)', url)
    return m.group(1) if m else url[:20]

def ssh(cmd, timeout=15):
    r = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
         "-o", "BatchMode=yes", HONCHO, cmd],
        capture_output=True, text=True, timeout=timeout
    )
    return r.stdout.strip(), r.returncode

def curl_json(url, timeout=10):
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5
        )
        if r.returncode == 0 and r.stdout:
            return True, json.loads(r.stdout)
        return False, None
    except Exception:
        return False, None

def curl_post_json(url, payload, timeout=30):
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", str(timeout), "-X", "POST", url,
             "-H", "Content-Type: application/json",
             "-d", json.dumps(payload)],
            capture_output=True, text=True, timeout=timeout + 5
        )
        if r.returncode == 0 and r.stdout:
            return True, json.loads(r.stdout), time.time()
        return False, None, time.time()
    except Exception:
        return False, None, time.time()

def curl_text(url, timeout=10):
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5
        )
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None

# ── Collect data ──────────────────────────────────────────────

alerts = []

# 1. API
ok, data = curl_json("http://100.67.206.76:8000/health")
api_ok = ok and isinstance(data, dict) and data.get("status") == "ok"
if not api_ok: alerts.append("API down")

# 2. Containers
out, _ = ssh("cd /opt/honcho/honcho && sudo docker compose ps --format '{{.Name}} {{.Status}}'", timeout=20)
containers = {}
for line in out.splitlines():
    parts = line.strip().split(None, 1)
    if len(parts) == 2:
        name = parts[0].replace("honcho-", "").replace("-1", "")
        status = parts[1]
        up = status.startswith("Up")
        containers[name] = {"status": status, "up": up}

db_ok = containers.get("database", {}).get("up", False)
redis_ok = containers.get("redis", {}).get("up", False)
deriver_up = containers.get("deriver", {}).get("up", False)
if not db_ok: alerts.append("DB down")
if not redis_ok: alerts.append("Redis down")
if not deriver_up: alerts.append("Deriver down")

# 3. Pipeline model config from API container
env_raw, _ = ssh("docker exec honcho-api-1 env") if api_ok else ("", 1)
models = {}
urls = {}
for line in env_raw.splitlines():
    if "=" not in line: continue
    key, val = line.split("=", 1)
    mapping = {
        "EMBEDDING_MODEL_CONFIG__MODEL": ("embed", models),
        "EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL": ("embed", urls),
        "DERIVER_MODEL_CONFIG__MODEL": ("deriver", models),
        "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL": ("deriver", urls),
        "SUMMARY_MODEL_CONFIG__MODEL": ("summary", models),
        "SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL": ("summary", urls),
        "DREAM_DEDUCTION_MODEL_CONFIG__MODEL": ("dream", models),
        "DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL": ("dream", urls),
        "DIALECTIC_LEVELS__low__MODEL_CONFIG__MODEL": ("dialectic", models),
        "DIALECTIC_LEVELS__low__MODEL_CONFIG__OVERRIDES__BASE_URL": ("dialectic", urls),
    }
    if key in mapping:
        stage, target = mapping[key]
        target[stage] = val

# Check embedding config matches endpoint
embed_config_ok = "11435" in urls.get("embed", "")
if not embed_config_ok and api_ok: alerts.append("Embed misconfigured")

# 4. Embedding endpoint
ok, data = curl_json(f"{SPARK_GOAT_EMBED}/v1/models")
embed_endpoint_ok = ok and isinstance(data, dict) and len(data.get("data", [])) > 0
if not embed_endpoint_ok: alerts.append("Embed endpoint down")

# 5. spark-goat chat speed test
t0 = time.time()
ok, data, t1 = curl_post_json("http://100.69.54.37:8001/v1/chat/completions", {
    "model": "aeon-ultimate",
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 20,
    "chat_template_kwargs": {"enable_thinking": False}
}, timeout=30)
spark_goat_dt = t1 - t0
spark_goat_ok = False
thinking = False
if ok and data:
    ch = data.get("choices", [{}])[0].get("message", {})
    spark_goat_ok = bool(ch.get("content"))
    thinking = bool(ch.get("reasoning_content"))

# 6. Deriver exporter metrics
metrics = curl_text(DERIVER_EXPORTER, timeout=8)
exp = {}
if metrics:
    def metric(name):
        m = re.search(rf'{name} ([\d.]+)', metrics)
        return float(m.group(1)) if m else None
    exp["done"] = int(metric('honcho_deriver_queue_completed_total') or 0)
    exp["concl"] = int(metric('honcho_deriver_conclusions_total') or 0)
    exp["errs"] = int(metric('honcho_deriver_errors_recent') or 0)
    oom = metric('honcho_deriver_container_oomkilled')
    if oom and oom > 0: alerts.append("Deriver OOM!")

# 7. Queue
out, _ = ssh('docker exec honcho-database-1 psql -U postgres -t -A -c "SELECT processed,count(*) FROM queue GROUP BY processed;"')
pending = done = 0
for line in out.split("\n"):
    if not line.strip(): continue
    parts = line.split("|")
    if len(parts) == 2:
        if parts[0] == "f": pending = int(parts[1])
        elif parts[0] == "t": done = int(parts[1])

# 8. Deriver log stats
runs_15m = "?"
last_dur = "?"
if deriver_up:
    ssh("docker logs --since=15m honcho-deriver-1 > /tmp/dlog_mon.txt 2>&1")
    r_out, _ = ssh("grep -c 'Observation Count' /tmp/dlog_mon.txt")
    runs_15m = r_out or "0"
    l_out, _ = ssh("grep 'Llm Call Duration' /tmp/dlog_mon.txt | tail -1")
    m = re.search(r'([\d,]+)\s+ms', l_out)
    last_dur = f"{int(m.group(1).replace(',',''))/1000:.0f}s" if m else "?"

# 9. Fork patch (only check running containers)
fork_issues = []
for c in ["honcho-api-1", "honcho-deriver-1"]:
    cname = c.replace("honcho-", "").replace("-1", "")
    if not containers.get(cname, {}).get("up"): continue
    p, _ = ssh(f"docker exec {c} grep -c _known /app/src/llm/backends/openai.py 2>/dev/null || echo 0")
    if p.strip() != "2":
        fork_issues.append(cname)
if fork_issues:
    alerts.append(f"Fork patch missing: {', '.join(fork_issues)}")

# ── Format output ─────────────────────────────────────────────

ts = datetime.utcnow().strftime("%H:%M UTC")
lines = [f"🩺 Honcho — {ts}"]

# Status row
s = lambda ok: "🟢" if ok else "🔴"
lines.append(f"{s(api_ok)} API  {s(deriver_up)} Deriver  {s(db_ok)} DB  {s(redis_ok)} Redis")

# Pipeline stages with model @ host
stages = [
    ("Embedding", "embed"),
    ("Deriver", "deriver"),
    ("Summary", "summary"),
    ("Dream", "dream"),
    ("Dialectic", "dialectic"),
]
for label, key in stages:
    model = models.get(key, "?")
    host = short_host(urls.get(key, ""))
    lines.append(f"  {label}: {model} @ {host}")

# spark-goat chat endpoint
think_tag = " ⚠️thinking" if thinking else ""
lines.append(f"{s(spark_goat_ok)} spark-goat chat: {spark_goat_dt:.1f}s{think_tag}")

# Queue
pct = f"({done/(pending+done)*100:.0f}%)" if (pending+done) > 0 else ""
lines.append(f"📋 Queue: {pending} pending · {done} done {pct}")

# Deriver activity
if deriver_up and exp:
    lines.append(f"⚡ {runs_15m} runs/15m · last={last_dur} · {exp['concl']} total conclusions")

# Alerts / delivery semantics
# This cron job runs with no_agent=True. Empty stdout means SILENT.
# Only emit a message when something needs attention.
if alerts:
    lines.append(f"⚠️ {len(alerts)}: {'; '.join(alerts)}")
    print("\n".join(lines))
