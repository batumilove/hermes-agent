#!/usr/bin/env python3
"""Langfuse workflow smoke pack for Hermes/Honcho.

This smoke pack verifies:
- Langfuse endpoint health
- Langfuse API auth/trace lookup using the configured keys
- Latest Hermes session that already has a trace in Langfuse
- Honcho container LANGFUSE_* env presence (names only)
- Recent export errors from Hermes and Honcho logs

Default Langfuse host:
    https://langfuse.batumi.works:8443

Default Honcho host:
    ubuntu@100.67.206.76
    workdir: /opt/honcho/honcho

Usage:
    ./langfuse-workflow-smoke.py
    ./langfuse-workflow-smoke.py --session-id 20260528_134002_1a2b3c
    ./langfuse-workflow-smoke.py --honcho-host ubuntu@100.67.206.76
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_LANGFUSE_HOST = "https://langfuse.batumi.works:8443"
DEFAULT_HONCHO_HOST = "ubuntu@100.67.206.76"
DEFAULT_HONCHO_WORKDIR = "/opt/honcho/honcho"


def runtime_hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


DEFAULT_HERMES_HOME = runtime_hermes_home()
DEFAULT_STATE_DB = DEFAULT_HERMES_HOME / "state.db"
DEFAULT_GW_LOG = DEFAULT_HERMES_HOME / "logs" / "gateway.log"
DEFAULT_ERR_LOG = DEFAULT_HERMES_HOME / "logs" / "errors.log"
DEFAULT_HERMES_AGENT = DEFAULT_HERMES_HOME / "hermes-agent"
DEFAULT_INFISICAL_CREDS = Path("/home/ubuntu/.config/infisical/hermes-machine.env")


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip().strip('"').strip("'")
        data[k] = v
    return data


def infisical_request_json(method: str, url: str, *, token: str | None = None, body: dict | None = None, timeout: int = 30) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def load_langfuse_creds_from_infisical(creds_path: Path = DEFAULT_INFISICAL_CREDS) -> tuple[str, str, str] | None:
    cfg = load_env_file(creds_path)
    required = ["INFISICAL_API_URL", "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_ID"]
    if any(not cfg.get(k) for k in required):
        return None
    api = cfg["INFISICAL_API_URL"].rstrip("/")
    env_name = cfg.get("INFISICAL_ENV", "prod")
    secret_path = cfg.get("INFISICAL_SECRET_PATH") or "/hermes/secrets"
    try:
        token_data = infisical_request_json(
            "POST",
            f"{api}/api/v1/auth/universal-auth/login",
            body={"clientId": cfg["INFISICAL_CLIENT_ID"], "clientSecret": cfg["INFISICAL_CLIENT_SECRET"]},
        )
        token = token_data["accessToken"]
        params = urlencode(
            {
                "projectId": cfg["INFISICAL_PROJECT_ID"],
                "environment": env_name,
                "secretPath": secret_path,
                "viewSecretValue": "true",
                "recursive": "true",
                "offset": "0",
                "limit": "100",
            }
        )
        resp = infisical_request_json("GET", f"{api}/api/v4/secrets?{params}", token=token)
        items = resp.get("secrets") or resp.get("data") or []
        exports: dict[str, str] = {}
        base = DEFAULT_LANGFUSE_HOST
        for item in items:
            key = item.get("secretKey") or item.get("key") or item.get("name")
            val = item.get("secretValue") or item.get("value")
            if key in {"LANGFUSE_PUBLIC_KEY", "HERMES_LANGFUSE_PUBLIC_KEY"} and val:
                exports["HERMES_LANGFUSE_PUBLIC_KEY"] = str(val)
            elif key in {"LANGFUSE_SECRET_KEY", "HERMES_LANGFUSE_SECRET_KEY"} and val:
                exports["HERMES_LANGFUSE_SECRET_KEY"] = str(val)
            elif key in {"LANGFUSE_BASE_URL", "HERMES_LANGFUSE_BASE_URL"} and val:
                base = str(val).strip()
        if "HERMES_LANGFUSE_PUBLIC_KEY" in exports and "HERMES_LANGFUSE_SECRET_KEY" in exports:
            return exports["HERMES_LANGFUSE_PUBLIC_KEY"], exports["HERMES_LANGFUSE_SECRET_KEY"], base or DEFAULT_LANGFUSE_HOST
    except Exception:
        return None
    return None


def require_langfuse_creds() -> tuple[str, str, str]:
    pk = env("HERMES_LANGFUSE_PUBLIC_KEY") or env("LANGFUSE_PUBLIC_KEY")
    sk = env("HERMES_LANGFUSE_SECRET_KEY") or env("LANGFUSE_SECRET_KEY")
    base = env("HERMES_LANGFUSE_BASE_URL") or env("LANGFUSE_BASE_URL") or DEFAULT_LANGFUSE_HOST
    if not (pk and sk):
        infisical_creds = load_langfuse_creds_from_infisical()
        if infisical_creds:
            pk, sk, base = infisical_creds
    missing = [name for name, val in [
        ("HERMES_LANGFUSE_PUBLIC_KEY / LANGFUSE_PUBLIC_KEY", pk),
        ("HERMES_LANGFUSE_SECRET_KEY / LANGFUSE_SECRET_KEY", sk),
    ] if not val]
    if missing:
        raise SystemExit(
            "Missing Langfuse credentials: " + ", ".join(missing) +
            "\nSet HERMES_LANGFUSE_PUBLIC_KEY and HERMES_LANGFUSE_SECRET_KEY (or the bare LANGFUSE_* aliases)."
        )
    return pk, sk, base.rstrip("/")


def http_json(url: str, pk: str, sk: str, *, timeout: int = 20) -> tuple[int, Any]:
    creds = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    req = Request(
        url,
        headers={
            "Authorization": f"Basic {creds}",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = raw
            return resp.status, data
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc


def http_text(url: str, *, timeout: int = 20) -> tuple[int, str]:
    req = Request(url, headers={"Accept": "application/json, text/plain;q=0.9, */*;q=0.1"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc


def run_cmd(cmd: list[str], *, timeout: int = 90, check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout.strip()}\n"
            f"stderr:\n{proc.stderr.strip()}"
        )
    return proc


def latest_sessions(state_db: Path, limit: int = 10) -> list[dict[str, Any]]:
    if not state_db.exists():
        raise RuntimeError(f"Missing Hermes state DB: {state_db}")
    conn = sqlite3.connect(str(state_db))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, source, title, started_at, ended_at, message_count
            FROM sessions
            WHERE COALESCE(message_count, 0) > 0
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def langfuse_traces_for_session(base_url: str, pk: str, sk: str, session_id: str, limit: int = 3) -> list[dict[str, Any]]:
    qs = urlencode({"sessionId": session_id, "limit": str(limit), "orderBy": "timestamp.desc"})
    status, data = http_json(f"{base_url}/api/public/traces?{qs}", pk, sk)
    if status != 200:
        raise RuntimeError(f"Langfuse trace query returned HTTP {status}")
    if isinstance(data, dict):
        return list(data.get("data", []))
    return []


def parse_iso(ts: str) -> str:
    return ts.replace("T", " ").replace("Z", " UTC") if ts else ""


def summarize_trace(trace: dict[str, Any]) -> str:
    meta = trace.get("metadata", {}) if isinstance(trace.get("metadata"), dict) else {}
    parts = [
        f"trace={trace.get('name') or '?'}",
        f"id={trace.get('id') or '?'}",
        f"time={parse_iso(trace.get('timestamp') or '')}",
    ]
    for key in ("provider", "model", "platform", "task_id", "workflow"):
        value = meta.get(key)
        if value:
            parts.append(f"{key}={value}")
    tags = trace.get("tags") or []
    if tags:
        parts.append(f"tags={','.join(tags[:6])}")
    obs = trace.get("observations") or []
    gen = next((o for o in obs if isinstance(o, dict) and o.get("type") == "GENERATION"), None)
    if gen:
        usage = gen.get("usage") or {}
        parts.append(f"tokens={usage.get('input', 0)}in/{usage.get('output', 0)}out")
    return " | ".join(parts)


def resolve_traced_session(base_url: str, pk: str, sk: str, session_candidates: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    tried: list[dict[str, Any]] = []
    for sess in session_candidates:
        sid = sess["id"]
        traces = langfuse_traces_for_session(base_url, pk, sk, sid, limit=1)
        tried.append({"session": sess, "trace_count": len(traces)})
        if traces:
            return sess, traces
    return None, tried


def honcho_remote(host: str, command: str, *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return run_cmd(["ssh", host, f"bash -lc {sh_quote(command)}"], timeout=timeout)


def honcho_env_presence(host: str, workdir: str, containers: list[str]) -> dict[str, list[str]]:
    listing = honcho_remote(host, f"cd {sh_quote(workdir)} && docker ps --format '{{{{.Names}}}}'", timeout=120)
    if listing.returncode != 0:
        raise RuntimeError(listing.stderr.strip() or listing.stdout.strip() or "honcho container listing failed")
    names = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
    resolved: dict[str, str] = {}
    for desired in containers:
        match = next(
            (
                n
                for n in names
                if n == desired or n.endswith("_" + desired) or n.endswith(desired)
            ),
            desired,
        )
        resolved[desired] = match

    out: dict[str, list[str]] = {}
    for desired in containers:
        actual = resolved[desired]
        cmd = f"cd {sh_quote(workdir)} && docker exec {sh_quote(actual)} sh -lc 'env | grep \"^LANGFUSE_\" | cut -d= -f1 | sort -u || true'"
        proc = honcho_remote(host, cmd, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"honcho env check failed for {actual}")
        out[desired] = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return out


def honcho_recent_export_errors(host: str, workdir: str, containers: list[str], tail: int = 200) -> dict[str, list[str]]:
    # Sample the recent logs and keep only likely export/tracing errors.
    pattern = r"(?i)(langfuse|otel|trace|export|observability|telemetry).*(error|fail|warn)|((error|fail|warn).*(langfuse|otel|trace|export|observability|telemetry))"
    results: dict[str, list[str]] = {}
    for container in containers:
        cmd = (
            f"cd {sh_quote(workdir)} && "
            f"docker logs --tail {int(tail)} {sh_quote(container)} 2>&1 | "
            f"grep -E {sh_quote(pattern)} || true"
        )
        proc = honcho_remote(host, cmd, timeout=120)
        lines = [ln.rstrip() for ln in proc.stdout.splitlines() if ln.strip()]
        results[container] = lines
    return results


def local_export_errors(log_paths: list[Path], tail: int = 200) -> dict[str, list[str]]:
    pattern = r"(?i)(langfuse|otel|trace|export|observability|telemetry).*(error|fail|warn)|((error|fail|warn).*(langfuse|otel|trace|export|observability|telemetry))"
    out: dict[str, list[str]] = {}
    for path in log_paths:
        if not path.exists():
            out[str(path)] = []
            continue
        try:
            text = path.read_text(errors="replace").splitlines()[-tail:]
        except Exception:
            out[str(path)] = ["<unreadable>"]
            continue
        out[str(path)] = [line for line in text if __import__("re").search(pattern, line)]
    return out


def sh_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def expand_langfuse_bases(base_url: str) -> list[str]:
    bases: list[str] = []

    def add(url: str) -> None:
        url = url.rstrip("/")
        if url and url not in bases:
            bases.append(url)

    add(base_url)
    # Fallback between the historical :8443 port and the bare host.
    if base_url.endswith(":8443"):
        add(base_url[: -len(":8443")])
    elif base_url.startswith("https://") and ":" not in base_url[len("https://"):]:
        add(base_url + ":8443")
    elif base_url.startswith("http://") and ":" not in base_url[len("http://"):]:
        add(base_url + ":8443")
    return bases


def main() -> int:
    parser = argparse.ArgumentParser(description="Langfuse workflow smoke pack for Hermes/Honcho")
    parser.add_argument("--session-id", help="Hermes session id to inspect. If omitted, the script searches the latest recent Hermes sessions until it finds one with a Langfuse trace.")
    parser.add_argument("--state-db", default=str(DEFAULT_STATE_DB), help="Hermes state.db path (default: ~/.hermes/state.db)")
    parser.add_argument("--langfuse-host", default=DEFAULT_LANGFUSE_HOST, help="Langfuse base URL")
    parser.add_argument("--honcho-host", default=DEFAULT_HONCHO_HOST, help="SSH target for the Honcho host")
    parser.add_argument("--honcho-workdir", default=DEFAULT_HONCHO_WORKDIR, help="Honcho repo workdir on the Honcho host")
    parser.add_argument("--recent-sessions", type=int, default=10, help="How many recent sessions to inspect when --session-id is omitted")
    parser.add_argument("--log-tail", type=int, default=200, help="How many log lines to sample for export-error checks")
    args = parser.parse_args()

    pk, sk, base_url = require_langfuse_creds()
    state_db = Path(args.state_db).expanduser()
    base_candidates = expand_langfuse_bases(base_url)

    checks: list[CheckResult] = []

    # 1) Langfuse endpoint health + auth; accept either the configured base or
    # the historical bare-host fallback if the ported URL is closed.
    active_base = None
    health_error = None
    auth_error = None
    for candidate in base_candidates:
        try:
            status, health_body = http_text(f"{candidate}/api/public/health")
            if status != 200:
                health_error = f"{candidate}/api/public/health -> {status}: {health_body[:120]}"
                continue
            status, _ = http_json(f"{candidate}/api/public/traces?{urlencode({'limit': '1', 'orderBy': 'timestamp.desc'})}", pk, sk)
            if status != 200:
                auth_error = f"{candidate}/api/public/traces?limit=1 -> {status}"
                continue
            active_base = candidate
            checks.append(CheckResult("langfuse_health", True, f"{candidate}/api/public/health -> 200"))
            checks.append(CheckResult("langfuse_auth", True, f"{candidate}/api/public/traces?limit=1 -> 200"))
            break
        except Exception as exc:
            if health_error is None:
                health_error = str(exc)
            else:
                auth_error = str(exc)
    if active_base is None:
        checks.append(CheckResult("langfuse_health", False, health_error or f"unable to reach any Langfuse base: {', '.join(base_candidates)}"))
        checks.append(CheckResult("langfuse_auth", False, auth_error or f"unable to auth against any Langfuse base: {', '.join(base_candidates)}"))
        active_base = base_candidates[0]

    # 2) Hermes trace lookup against the same base we validated above.
    trace_session = None
    trace_rows: list[dict[str, Any]] = []
    trace_attempts: list[dict[str, Any]] = []
    trace_base = active_base
    try:
        status, payload = http_json(
            f"{trace_base}/api/public/traces?{urlencode({'limit': '1', 'orderBy': 'timestamp.desc'})}",
            pk,
            sk,
        )
        latest_trace = (payload.get("data") or [{}])[0] if isinstance(payload, dict) else {}
        latest_session_id = latest_trace.get("sessionId")
        if latest_session_id:
            trace_session = {"id": latest_session_id, "source": "langfuse", "title": latest_trace.get("name"), "message_count": None}
            trace_rows = [latest_trace]
            trace_attempts = [{"session": trace_session, "trace_count": 1}]
        elif args.session_id:
            candidates = [{"id": args.session_id, "source": "manual", "title": None, "message_count": None}]
            trace_session, trace_attempts = resolve_traced_session(trace_base, pk, sk, candidates)
            if trace_session:
                trace_rows = langfuse_traces_for_session(trace_base, pk, sk, trace_session["id"], limit=3)
        else:
            candidates = latest_sessions(state_db, limit=args.recent_sessions)
            trace_session, trace_attempts = resolve_traced_session(trace_base, pk, sk, candidates)
            if trace_session:
                trace_rows = langfuse_traces_for_session(trace_base, pk, sk, trace_session["id"], limit=3)
    except Exception as exc:
        checks.append(CheckResult("hermes_trace_lookup", False, str(exc)))
    if trace_session and trace_rows:
        checks.append(CheckResult("hermes_trace_lookup", True, f"found trace for session {trace_session['id']}"))
    elif not any(c.name == "hermes_trace_lookup" for c in checks):
        if args.session_id:
            checks.append(CheckResult("hermes_trace_lookup", False, f"no Langfuse trace found for requested session {args.session_id}"))
        else:
            checks.append(CheckResult("hermes_trace_lookup", False, f"no traces found for the {args.recent_sessions} most recent Hermes sessions"))

    # 3) Honcho env presence (names only)
    honcho_containers = ["honcho-api-1", "honcho-deriver-1"]
    env_presence: dict[str, list[str]] = {}
    try:
        env_presence = honcho_env_presence(args.honcho_host, args.honcho_workdir, honcho_containers)
        checks.append(CheckResult("honcho_env", True, f"checked {args.honcho_host} containers {', '.join(honcho_containers)}"))
    except Exception as exc:
        checks.append(CheckResult("honcho_env", False, str(exc)))

    # 4) Recent export errors
    honcho_export_errors: dict[str, list[str]] = {}
    try:
        honcho_export_errors = honcho_recent_export_errors(args.honcho_host, args.honcho_workdir, honcho_containers, tail=args.log_tail)
        checks.append(CheckResult("honcho_export_errors", True, f"sampled tail {args.log_tail} from {', '.join(honcho_containers)}"))
    except Exception as exc:
        checks.append(CheckResult("honcho_export_errors", False, str(exc)))

    local_logs = [DEFAULT_GW_LOG, DEFAULT_ERR_LOG]
    local_errors = local_export_errors(local_logs, tail=args.log_tail)
    if any(local_errors.values()):
        checks.append(CheckResult("hermes_local_export_errors", True, "found some matches in local logs"))
    else:
        checks.append(CheckResult("hermes_local_export_errors", True, "no matches in local logs"))

    # Emit report
    print("Langfuse workflow smoke pack")
    print(f"Langfuse host: {active_base}")
    if active_base != base_url:
        print(f"Configured Langfuse base: {base_url}")
    print()

    for check in checks:
        state = "OK" if check.ok else "FAIL"
        print(f"{state} {check.name}: {check.detail}")

    print()
    if trace_session and trace_rows:
        session_title = trace_session.get("title") or ""
        session_src = trace_session.get("source") or ""
        session_msgs = trace_session.get("message_count")
        title_bits = []
        if session_title:
            title_bits.append(f"title={session_title}")
        if session_src:
            title_bits.append(f"source={session_src}")
        if session_msgs is not None:
            title_bits.append(f"messages={session_msgs}")
        suffix = f" ({', '.join(title_bits)})" if title_bits else ""
        print(f"Latest Hermes session with a Langfuse trace: {trace_session['id']}{suffix}")
        print(f"Latest trace: {summarize_trace(trace_rows[0])}")
    elif args.session_id:
        print(f"No Langfuse trace found for requested session: {args.session_id}")
    else:
        print("No Langfuse trace found in the sampled Hermes sessions.")
        if trace_attempts:
            scanned = ", ".join(
                f"{item['session']['id']}({item['trace_count']})" for item in trace_attempts[:5]
            )
            print(f"Scanned sessions: {scanned}")

    print()
    print("Honcho Langfuse env presence (names only):")
    for container in honcho_containers:
        names = env_presence.get(container, [])
        print(f"  {container}: {', '.join(names) if names else 'none'}")

    print()
    print("Recent export errors:")
    print(f"  local {DEFAULT_GW_LOG.name}: {len(local_errors.get(str(DEFAULT_GW_LOG), []))} sampled match(es)")
    print(f"  local {DEFAULT_ERR_LOG.name}: {len(local_errors.get(str(DEFAULT_ERR_LOG), []))} sampled match(es)")
    for container in honcho_containers:
        lines = honcho_export_errors.get(container, [])
        if not lines:
            print(f"  {container}: none in sampled logs")
        else:
            print(f"  {container}: {len(lines)} sampled match(es)")
            for line in lines[:5]:
                print(f"    - {line}")

    failed = any(not c.ok for c in checks if c.name not in {"hermes_local_export_errors"})
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
