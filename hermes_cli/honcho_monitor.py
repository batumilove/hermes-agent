"""Honcho production smoke monitor.

This module powers the standalone cron/watchdog script in ``scripts/honcho_monitor.py``.
It keeps the output compact and model-aware while checking for the silent-failure
cases that matter in production:

- loaded embedding config in the Honcho API container
- DB document/message embedding dimensions
- queue/doc growth deltas between runs
- recent representation-save / 401 errors from the deriver
- spark-goat / spark-polarbear chat latency, not just model liveness
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HONCHO_TARGET = os.environ.get("HONCHO_MONITOR_HOST", "ubuntu@100.67.206.76")
STATE_PATH = Path(
    os.environ.get(
        "HONCHO_MONITOR_STATE",
        str(Path.home() / ".hermes" / "cache" / "honcho_monitor.json"),
    )
)

HOST_MAP = {
    "100.69.54.37:8001": "spark-goat",
    "100.69.54.37:11435": "spark-goat",
    "100.71.155.95:8001": "spark-polarbear",
    "100.71.155.95:11435": "spark-polarbear",
    "100.110.104.77:8087": "mac-studio",
    "192.168.100.14:8088": "mac-horse",
    "api.openai.com": "openai",
    "openrouter.ai/api/v1": "openrouter",
}


@dataclass(frozen=True)
class HonchoSnapshot:
    services: dict[str, bool]
    pipeline: dict[str, dict[str, str]]
    db: dict[str, Any]
    queue: dict[str, int]
    errors: dict[str, int]
    spark_goat: dict[str, Any]
    deriver: dict[str, Any]


def short_host(url: str | None) -> str:
    if not url:
        return "?"
    for needle, short in HOST_MAP.items():
        if needle in url:
            return short
    match = re.search(r"://([^/]+)", url)
    return match.group(1) if match else url[:24]


def mask(value: str | None) -> str:
    if not value:
        return "****"
    value = str(value)
    if len(value) < 8:
        return "****"
    return f"{value[:4]}{'*' * max(4, len(value) - 4)}"


def parse_pipeline_env(raw: str) -> dict[str, dict[str, str]]:
    """Extract the pipeline model config from ``docker exec ... env`` output."""
    fields = {
        "embedding": {
            "model": "EMBEDDING_MODEL_CONFIG__MODEL",
            "base_url": "EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL",
            "dimensions_mode": "EMBEDDING_MODEL_CONFIG__DIMENSIONS_MODE",
            "vector_dimensions": "EMBEDDING_VECTOR_DIMENSIONS",
        },
        "deriver": {
            "model": "DERIVER_MODEL_CONFIG__MODEL",
            "base_url": "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL",
        },
        "summary": {
            "model": "SUMMARY_MODEL_CONFIG__MODEL",
            "base_url": "SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL",
        },
        "dream": {
            "model": "DREAM_DEDUCTION_MODEL_CONFIG__MODEL",
            "base_url": "DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL",
        },
        "dialectic": {
            "model": "DIALECTIC_LEVELS__low__MODEL_CONFIG__MODEL",
            "base_url": "DIALECTIC_LEVELS__low__MODEL_CONFIG__OVERRIDES__BASE_URL",
        },
    }

    env: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()

    parsed: dict[str, dict[str, str]] = {}
    for stage, mapping in fields.items():
        parsed[stage] = {out_key: env.get(in_key, "") for out_key, in_key in mapping.items()}
    return parsed


def load_state(path: Path | str = STATE_PATH) -> dict[str, Any]:
    state_path = Path(path)
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save_state(path: Path | str, state: dict[str, Any]) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def _now_utc(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _fmt_s(value: Any) -> str:
    try:
        return f"{float(value):.1f}s"
    except Exception:
        return "?"


def _service_row(services: dict[str, bool]) -> str:
    labels = (("api_ok", "API"), ("deriver_up", "Deriver"), ("db_ok", "DB"), ("redis_ok", "Redis"))
    return " ".join(
        f"{'🟢' if services.get(key, False) else '🔴'} {label}"
        for key, label in labels
    )


def _trend_line(snapshot: HonchoSnapshot, previous_state: dict[str, Any] | None) -> str | None:
    if not previous_state:
        return None

    prev_queue = int(previous_state.get("queue_done", snapshot.queue.get("done", 0)))
    prev_docs = int(previous_state.get("documents_total", snapshot.db.get("documents_total", 0)))
    queue_delta = max(0, int(snapshot.queue.get("done", 0)) - prev_queue)
    docs_delta = max(0, int(snapshot.db.get("documents_total", 0)) - prev_docs)
    return f"Δ15m: queue +{queue_delta} · docs +{docs_delta}"


def build_alerts(snapshot: HonchoSnapshot, previous_state: dict[str, Any] | None = None) -> list[str]:
    alerts: list[str] = []

    svc = snapshot.services
    for key, label in (("api_ok", "API"), ("deriver_up", "Deriver"), ("db_ok", "DB"), ("redis_ok", "Redis")):
        if not svc.get(key, False):
            alerts.append(f"{label} down")

    embed = snapshot.pipeline.get("embedding", {})
    embed_model = embed.get("model", "")
    embed_url = embed.get("base_url", "")
    vector_dims = (embed.get("vector_dimensions") or "").strip()
    if not embed_model or not embed_url:
        alerts.append("Embedding config missing")
    elif "api.openai.com" in embed_url or embed_model == "text-embedding-3-small":
        alerts.append("Embedding config looks like OpenAI fallback")

    doc_dims = snapshot.db.get("documents_dims")
    msg_dims = snapshot.db.get("messages_dims")
    if vector_dims and doc_dims not in (None, "", 0, "0") and str(doc_dims) != vector_dims:
        alerts.append("Document embedding dims mismatch")
    if vector_dims and msg_dims not in (None, "", 0, "0") and str(msg_dims) != vector_dims:
        alerts.append("Message embedding dims mismatch")

    if int(snapshot.errors.get("save_representation", 0)) > 0 or int(snapshot.errors.get("four_oh_one", 0)) > 0:
        alerts.append("Recent representation save / 401 errors")

    spark = snapshot.spark_goat
    if spark.get("thinking"):
        alerts.append("spark-goat thinking still enabled")
    if spark.get("ok") and float(spark.get("latency_s", 0.0) or 0.0) > 5.0:
        alerts.append("spark-goat chat latency degraded")

    if previous_state:
        prev_queue = int(previous_state.get("queue_done", snapshot.queue.get("done", 0)))
        prev_docs = int(previous_state.get("documents_total", snapshot.db.get("documents_total", 0)))
        queue_delta = max(0, int(snapshot.queue.get("done", 0)) - prev_queue)
        docs_delta = max(0, int(snapshot.db.get("documents_total", 0)) - prev_docs)
        if queue_delta > docs_delta:
            alerts.append("Queue advancing faster than documents")

    return alerts


def format_report(snapshot: dict[str, Any] | HonchoSnapshot, previous_state: dict[str, Any] | None = None, now: datetime | None = None) -> str:
    if isinstance(snapshot, dict):
        snapshot = HonchoSnapshot(
            services=dict(snapshot.get("services", {})),
            pipeline={k: dict(v) for k, v in snapshot.get("pipeline", {}).items()},
            db=dict(snapshot.get("db", {})),
            queue={k: int(v) for k, v in snapshot.get("queue", {}).items()},
            errors={k: int(v) for k, v in snapshot.get("errors", {}).items()},
            spark_goat=dict(snapshot.get("spark_goat", {})),
            deriver=dict(snapshot.get("deriver", {})),
        )

    lines = [f"🩺 Honcho — {_now_utc(now).strftime('%H:%M UTC')}"]
    lines.append(_service_row(snapshot.services))

    for label, key in (
        ("Embedding", "embedding"),
        ("Deriver", "deriver"),
        ("Summary", "summary"),
        ("Dream", "dream"),
        ("Dialectic", "dialectic"),
    ):
        stage = snapshot.pipeline.get(key, {})
        lines.append(f"  {label}: {stage.get('model', '?')} @ {short_host(stage.get('base_url'))}")

    embed = snapshot.pipeline.get("embedding", {})
    lines.append(
        "  Embedding env: "
        f"model={embed.get('model', '?')} "
        f"base_url={embed.get('base_url', '?')} "
        f"dims_mode={embed.get('dimensions_mode', '?')} "
        f"vector_dims={embed.get('vector_dimensions', '?')}"
    )

    lines.append(
        "  Embedding DB: "
        f"docs {snapshot.db.get('documents_with_embeddings', 0)}/{snapshot.db.get('documents_total', 0)} "
        f"dims={snapshot.db.get('documents_dims', '?')} · "
        f"messages {snapshot.db.get('messages_with_embeddings', 0)}/{snapshot.db.get('messages_total', 0)} "
        f"dims={snapshot.db.get('messages_dims', '?')}"
    )

    trend = _trend_line(snapshot, previous_state)
    if trend:
        lines.append(f"  {trend}")

    lines.append(f"📋 Queue: {snapshot.queue.get('pending', 0)} pending · {snapshot.queue.get('done', 0)} done")
    lines.append(
        f"⚠️ Recent errors: save-repr={snapshot.errors.get('save_representation', 0)} · 401={snapshot.errors.get('four_oh_one', 0)}"
    )

    spark = snapshot.spark_goat
    spark_tag = " ⚠️thinking" if spark.get("thinking") else ""
    lines.append(f"{ '🟢' if spark.get('ok') else '🔴' } spark-goat chat: {_fmt_s(spark.get('latency_s'))}{spark_tag}")

    deriver = snapshot.deriver
    if deriver:
        lines.append(
            f"⚡ {deriver.get('runs_15m', '?')} runs/15m · last={_fmt_s(deriver.get('last_duration_s'))} · {deriver.get('conclusions', '?')} total conclusions"
        )

    alerts = build_alerts(snapshot, previous_state=previous_state)
    if alerts:
        lines.append(f"⚠️ {len(alerts)}: {'; '.join(alerts)}")
    else:
        lines.append("✅ All nominal")

    return "\n".join(lines)


def ssh(command: str, timeout: int = 20) -> str:
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            HONCHO_TARGET,
            command,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


def curl_json(url: str, timeout: int = 10) -> tuple[bool, dict[str, Any] | None]:
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", str(timeout), url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        if result.returncode != 0 or not result.stdout:
            return False, None
        return True, json.loads(result.stdout)
    except Exception:
        return False, None


def curl_text(url: str, timeout: int = 10) -> str:
    try:
        result = subprocess.run(
            ["curl", "-s", "-m", str(timeout), url],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        return result.stdout if result.returncode == 0 else ""
    except Exception:
        return ""


def curl_post_json(url: str, payload: dict[str, Any], timeout: int = 30) -> tuple[bool, dict[str, Any] | None, float]:
    start = time.perf_counter()
    try:
        result = subprocess.run(
            [
                "curl",
                "-s",
                "-m",
                str(timeout),
                "-X",
                "POST",
                url,
                "-H",
                "Content-Type: application/json",
                "-d",
                json.dumps(payload),
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        dt = time.perf_counter() - start
        if result.returncode != 0 or not result.stdout:
            return False, None, dt
        return True, json.loads(result.stdout), dt
    except Exception:
        return False, None, time.perf_counter() - start


def _parse_service_status(raw: str) -> dict[str, bool]:
    services = {"api_ok": False, "deriver_up": False, "db_ok": False, "redis_ok": False}
    for line in raw.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        name, status = parts
        key = name.replace("honcho-", "").replace("-1", "")
        up = status.startswith("Up")
        if key == "api":
            services["api_ok"] = up
        elif key == "deriver":
            services["deriver_up"] = up
        elif key == "database":
            services["db_ok"] = up
        elif key == "redis":
            services["redis_ok"] = up
    return services


def _parse_kv_lines(raw: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in raw.splitlines():
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) != 2:
            continue
        data[parts[0]] = parts[1]
    return data


def _int_or_zero(value: str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except Exception:
        return 0


def collect_snapshot() -> tuple[HonchoSnapshot, dict[str, Any]]:
    services_raw = ssh("cd /opt/honcho/honcho && sudo docker compose ps --format '{{.Name}} {{.Status}}'", timeout=30)
    services = _parse_service_status(services_raw)

    env_raw = ssh("docker exec honcho-api-1 env", timeout=20) if services.get("api_ok") else ""
    pipeline = parse_pipeline_env(env_raw)

    # One row from documents plus subqueries for message embeddings keeps the
    # command compact while still verifying both vector dimensions.
    db_stats_raw = ssh(
        "docker exec honcho-database-1 psql -U postgres -t -A -F '|' -c \""
        "SELECT "
        "count(*) FILTER (WHERE deleted_at IS NULL)::text, "
        "count(*) FILTER (WHERE deleted_at IS NULL AND embedding IS NOT NULL)::text, "
        "COALESCE((SELECT min(array_length(embedding::real[], 1))::text FROM documents WHERE deleted_at IS NULL AND embedding IS NOT NULL), '')::text, "
        "(SELECT count(*)::text FROM message_embeddings), "
        "(SELECT count(*)::text FROM message_embeddings WHERE embedding IS NOT NULL), "
        "COALESCE((SELECT min(array_length(embedding::real[], 1))::text FROM message_embeddings WHERE embedding IS NOT NULL), '')::text "
        "FROM documents;\"",
        timeout=30,
    )
    db_parts = [part.strip() for part in db_stats_raw.split("|")] if db_stats_raw else []
    db = {
        "documents_total": _int_or_zero(db_parts[0]) if len(db_parts) > 0 else 0,
        "documents_with_embeddings": _int_or_zero(db_parts[1]) if len(db_parts) > 1 else 0,
        "documents_dims": _int_or_zero(db_parts[2]) if len(db_parts) > 2 and db_parts[2] else 0,
        "messages_total": _int_or_zero(db_parts[3]) if len(db_parts) > 3 else 0,
        "messages_with_embeddings": _int_or_zero(db_parts[4]) if len(db_parts) > 4 else 0,
        "messages_dims": _int_or_zero(db_parts[5]) if len(db_parts) > 5 and db_parts[5] else 0,
    }

    queue_raw = ssh(
        "docker exec honcho-database-1 psql -U postgres -t -A -F '|' -c \""
        "SELECT processed::text, count(*)::text FROM queue GROUP BY processed ORDER BY processed;\"",
        timeout=20,
    )
    pending = done = 0
    for row in queue_raw.splitlines():
        state, count = (row.split("|", 1) + ["0"])[:2]
        if state == "f":
            pending = _int_or_zero(count)
        elif state == "t":
            done = _int_or_zero(count)

    errors_raw = ssh(
        "docker logs honcho-deriver-1 --since 15m > /tmp/honcho-deriver-monitor.log 2>&1; "
        "printf 'save|%s\n' \"$(grep -c 'Failed to save representation' /tmp/honcho-deriver-monitor.log || true)\"; "
        "printf '401|%s\n' \"$(grep -c '401' /tmp/honcho-deriver-monitor.log || true)\"",
        timeout=30,
    )
    error_counts = _parse_kv_lines(errors_raw)

    spark_ok, spark_data, spark_dt = curl_post_json(
        "http://100.69.54.37:8001/v1/chat/completions",
        {
            "model": pipeline.get("deriver", {}).get("model") or "aeon-ultimate",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=30,
    )
    thinking = False
    if spark_data:
        msg = (spark_data.get("choices") or [{}])[0].get("message", {})
        thinking = bool(msg.get("reasoning_content"))
        spark_ok = spark_ok and bool(msg.get("content"))

    logs_raw = ssh(
        "docker logs honcho-deriver-1 --since 15m > /tmp/honcho-deriver-monitor.log 2>&1; "
        "printf 'count|%s\n' \"$(grep -c 'Observation Count' /tmp/honcho-deriver-monitor.log || true)\"; "
        "printf 'last|%s\n' \"$(grep 'Llm Call Duration' /tmp/honcho-deriver-monitor.log | tail -1 || true)\"; "
        "printf 'concl|%s\n' \"$(grep -o '[0-9][0-9]* total conclusions' /tmp/honcho-deriver-monitor.log | tail -1 || true)\"",
        timeout=30,
    )
    log_parts = _parse_kv_lines(logs_raw)
    runs_15m = _int_or_zero(log_parts.get("count", "0"))
    conclusions = 0
    concl_match = re.search(r"(\d+)", log_parts.get("concl", "") or "")
    if concl_match:
        conclusions = _int_or_zero(concl_match.group(1))
    last_duration_s = 0
    last_match = re.search(r"([\d,]+)\s+ms", log_parts.get("last", ""))
    if last_match:
        try:
            last_duration_s = int(int(last_match.group(1).replace(",", "")) / 1000)
        except Exception:
            last_duration_s = 0

    snapshot = HonchoSnapshot(
        services=services,
        pipeline=pipeline,
        db=db,
        queue={"pending": pending, "done": done},
        errors={"save_representation": _int_or_zero(error_counts.get("save", "0")), "four_oh_one": _int_or_zero(error_counts.get("401", "0"))},
        spark_goat={"ok": spark_ok, "latency_s": spark_dt, "thinking": thinking},
        deriver={"runs_15m": runs_15m, "last_duration_s": last_duration_s, "conclusions": conclusions},
    )

    return snapshot, {"queue_done": done, "documents_total": db.get("documents_total", 0), "messages_total": db.get("messages_total", 0)}


def main() -> int:
    snapshot, current_state = collect_snapshot()
    previous_state = load_state(STATE_PATH)
    report = format_report(snapshot, previous_state=previous_state)
    print(report)
    save_state(STATE_PATH, current_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
