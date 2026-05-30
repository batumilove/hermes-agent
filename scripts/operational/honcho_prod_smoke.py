#!/usr/bin/env python3
"""Honcho production smoke test with human-readable output."""
import subprocess,json,shlex,time,sys,urllib.request,urllib.error
def utc_now(): import datetime; return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def run(cmd,timeout=30):
    try:
        p=subprocess.run(cmd,text=True,capture_output=True,timeout=timeout); return {"ok":p.returncode==0,"rc":p.returncode,"stdout":p.stdout.strip(),"stderr":p.stderr.strip()[:1200]}
    except subprocess.TimeoutExpired as e: return {"ok":False,"rc":-1,"stdout":"","stderr":f"TimeoutError: {e}"}
def remote(host,script,timeout=60): return run(["ssh","-o","BatchMode=yes",host,"bash -lc "+shlex.quote(script)],timeout=timeout)
def http_json(url,payload=None,timeout=60):
    try:
        if payload is None:
            with urllib.request.urlopen(url,timeout=timeout) as r: return True,json.load(r),None
        req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=timeout) as r: return True,json.load(r),None
    except Exception as e: return False,None,str(e)
def fmt_ok(ok): return "✅" if ok else "❌"
def main():
    # Use normal SSH over the LAN for Honcho from the Hermes VM. Tailscale SSH currently
    # requires an interactive check URL from this host, which hangs unattended cron runs.
    HONCHO="ubuntu@192.168.100.18"; SPARK_GOAT="root@100.69.54.37"
    out={"generated_at":utc_now(),"checks":{}}
    lines=[]
    lines.append(f"Honcho Production Smoke — {out['generated_at']}")
    lines.append("")

    # Honcho health
    r=remote(HONCHO,"cd /opt/honcho/honcho && sudo docker compose ps --format json >/tmp/honcho-ps.json && curl -fsS http://127.0.0.1:8000/health",60); ok=r["ok"] and '{"status":"ok"}' in r["stdout"]; out["checks"]["honcho_health"]={"ok":ok}
    lines.append(f"  {fmt_ok(ok)} Honcho API health")

    # pgvector schema
    r=remote(HONCHO,"cd /opt/honcho/honcho && sudo docker compose exec -T database psql -U postgres -d postgres -At -c \"SELECT attrelid::regclass || ':' || atttypmod FROM pg_attribute WHERE attname='embedding' AND attrelid IN ('documents'::regclass, 'message_embeddings'::regclass) ORDER BY 1; SELECT 'hnsw:' || COUNT(*) FROM pg_indexes WHERE tablename IN ('documents','message_embeddings') AND indexdef ILIKE '%hnsw%';\"",60); ok=r["ok"] and "documents:1536" in r["stdout"] and "message_embeddings:1536" in r["stdout"] and "hnsw:2" in r["stdout"]; out["checks"]["honcho_pgvector_schema"]={"ok":ok}
    lines.append(f"  {fmt_ok(ok)} pgvector schema (1536d + HNSW)")

    # spark-goat embedding service
    r=remote(SPARK_GOAT,"echo enabled=$(systemctl is-enabled qwen3-embedding-vllm.service); echo active=$(systemctl is-active qwen3-embedding-vllm.service); docker ps --filter name=qwen3-embedding-vllm --format '{{.Names}} {{.Status}} {{.Ports}}'",30); ok=r["ok"] and "enabled=enabled" in r["stdout"] and "active=active" in r["stdout"] and ":11435->" in r["stdout"]; out["checks"]["spark_goat_embedding_service"]={"ok":ok}
    lines.append(f"  {fmt_ok(ok)} spark-goat embedding service")

    # Embedding models
    ok,data,err=http_json("http://100.69.54.37:11435/v1/models",timeout=20); models=[m.get("id") for m in (data or {}).get("data",[])] if ok else []; ok=ok and "qwen3-embedding-8b-1536" in models; out["checks"]["embedding_models"]={"ok":ok}
    lines.append(f"  {fmt_ok(ok)} Embedding models endpoint")

    # Embedding dim smoke
    t=time.time(); ok,data,err=http_json("http://100.69.54.37:11435/v1/embeddings",{"model":"qwen3-embedding-8b-1536","input":["honcho production smoke"],"encoding_format":"float"},180); dim=len(data["data"][0]["embedding"]) if ok and data and data.get("data") else None; ok=ok and dim==1536; out["checks"]["embedding_dim_smoke"]={"ok":ok,"seconds":round(time.time()-t,3)}
    lines.append(f"  {fmt_ok(ok)} Embedding smoke (1536d, {out['checks']['embedding_dim_smoke']['seconds']}s)")

    # Chat models
    chat_model="aeon-ultimate"
    ok,data,err=http_json("http://100.69.54.37:8001/v1/models",timeout=20); models=[m.get("id") for m in (data or {}).get("data",[])] if ok else []; ok=ok and chat_model in models; out["checks"]["chat_models"]={"ok":ok,"model":chat_model}
    lines.append(f"  {fmt_ok(ok)} Chat models endpoint ({chat_model})")

    # Chat completion smoke
    t=time.time(); ok,data,err=http_json("http://100.69.54.37:8001/v1/chat/completions",{"model":chat_model,"messages":[{"role":"user","content":"Reply exactly: ok"}],"max_tokens":16,"temperature":0,"chat_template_kwargs":{"enable_thinking":False}},600); msg=(data.get("choices") or [{}])[0].get("message",{}) if ok and data else {}; content=msg.get("content"); ok=bool(ok and content and "ok" in content.lower()); secs=round(time.time()-t,3); out["checks"]["chat_completion_smoke"]={"ok":ok,"seconds":secs,"model":chat_model}
    lines.append(f"  {fmt_ok(ok)} Chat completion smoke ({secs}s, {chat_model})")

    # Message pipeline (v3 API)
    ws="prod-smoke-"+str(int(time.time())); sess_id="sess-"+str(int(time.time())); script=f"""set -euo pipefail
cd /opt/honcho/honcho
base=http://127.0.0.1:8000
curl -fsS -X POST $base/v3/workspaces -H 'Content-Type: application/json' -d '{{"name":"{ws}"}}' >/dev/null
curl -fsS -X POST $base/v3/workspaces/{ws}/peers -H 'Content-Type: application/json' -d '{{"name":"{ws}-observer"}}' >/dev/null
curl -fsS -X POST $base/v3/workspaces/{ws}/sessions -H 'Content-Type: application/json' -d '{{"id":"{sess_id}","peers":{{"{ws}-observer":{{}}}}}}' >/dev/null
curl -fsS -X POST $base/v3/workspaces/{ws}/sessions/{sess_id}/messages -H 'Content-Type: application/json' -d '{{"messages":[{{"content":"production smoke test","peer_id":"{ws}-observer"}}]}}' >/dev/null
sleep 7
sudo docker compose exec -T database psql -U postgres -d postgres -At -c "SELECT COUNT(*) FROM message_embeddings WHERE workspace_name='{ws}' AND embedding IS NOT NULL;"
curl -fsS -X DELETE $base/v3/workspaces/{ws}/sessions/{sess_id} >/dev/null 2>&1 || true
curl -fsS -X DELETE $base/v3/workspaces/{ws} >/dev/null 2>&1 || true
"""; r=remote(HONCHO,script,150); ok=r["ok"] and r["stdout"].splitlines()[-1:]==["1"]; out["checks"]["honcho_message_pipeline"]={"ok":ok}
    lines.append(f"  {fmt_ok(ok)} Message pipeline (create + embed)")

    # Error scan
    # Filter by log severity first. Honcho DEBUG logs can contain benign request payloads
    # with substrings such as "url": "/embeddings" or queue-manager text; the previous
    # scanner grepped keywords across all severities and produced false alerts.
    scan_script="""cd /opt/honcho/honcho
sudo docker compose logs --since=90s api deriver 2>&1 \
  | grep -Ei ' - (ERROR|CRITICAL|EXCEPTION) - |Traceback|AuthenticationError|Error code: 401|embedding batch failed|dimension mismatch|matryoshka|Failed to generate' \
  | grep -Eiv 'uvicorn.error - INFO|Shutting down|Application shutdown complete|Application startup complete|Started server process|Finished server process|Waiting for application|Uvicorn running|INFO.*GET /health' || true
"""
    r=remote(HONCHO,scan_script,60); errors=r["stdout"].strip(); ok=not errors; out["checks"]["honcho_recent_error_scan"]={"ok":ok}
    if errors:
        # Show first 2 unique error lines
        err_lines=[l.strip() for l in errors.splitlines() if l.strip()][:4]
        lines.append(f"  {fmt_ok(ok)} Error scan — {len(err_lines)} issue(s):")
        for e in err_lines:
            lines.append(f"       {e[:150]}")
    else:
        lines.append(f"  {fmt_ok(ok)} Error scan — clean")

    # Backup timer
    r=remote(HONCHO,"echo enabled=$(systemctl is-enabled honcho-postgres-backup.timer); echo next=$(systemctl show honcho-postgres-backup.timer -P NextElapseUSecRealtime); latest=$(ls -1t /opt/honcho/backups/honcho-postgres-*.dump | head -1); echo latest=$latest; pg_restore -l \"$latest\" >/dev/null",60); ok=r["ok"] and "enabled=enabled" in r["stdout"] and "honcho-postgres-" in r["stdout"]; out["checks"]["honcho_backup_timer_restore_list"]={"ok":ok}
    lines.append(f"  {fmt_ok(ok)} Backup timer + restore-list")

    all_ok=all(c.get("ok") for c in out["checks"].values()); out["ok"]=all_ok
    lines.append("")
    status="ALL PASS" if all_ok else "FAILURES DETECTED"
    lines.append(f"  Overall: {fmt_ok(all_ok)} {status}")

    print("\n".join(lines))
    # Also write JSON for programmatic use
    with open("/tmp/honcho_smoke_last.json","w") as f: json.dump(out,f,indent=2,sort_keys=True)
    return 0 if all_ok else 2
if __name__=="__main__": raise SystemExit(main())
