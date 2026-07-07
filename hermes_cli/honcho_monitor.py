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

# Use Teleport SSH by default. The Honcho host is enrolled and available via
# the cron tbot config, which keeps unattended runs off the interactive LAN path.
# Keep HONCHO_MONITOR_HOST as an explicit escape hatch.
HONCHO_TARGET = os.environ.get("HONCHO_MONITOR_HOST", "ubuntu@honcho.teleport.batumi.works")
STATE_PATH = Path(
    os.environ.get(
        "HONCHO_MONITOR_STATE",
        str(Path.home() / ".hermes" / "cache" / "honcho_monitor.json"),
    )
)

HOST_MAP = {
    "192.168.10.211:8001": "spark-goat",
    "192.168.10.211:11435": "spark-goat",
    "100.69.54.37:8001": "spark-goat",
    "100.69.54.37:11435": "spark-goat",
    "100.71.155.95:8001": "spark-polarbear",
    "100.71.155.95:11435": "spark-polarbear",
    "100.110.104.77:8087": "mac-studio",
    "192.168.100.14:8088": "mac-horse",
    "api.openai.com": "openai",
    "openrouter.ai/api/v1": "openrouter",
}

# Prefer the LAN endpoint for spark-goat. The historical Tailscale address
# (100.69.54.37) can refuse :8001 while Honcho and Hermes both reach the live
# vLLM service through the current LAN address advertised in Honcho's loaded
# config.
SPARK_CHAT_BASE = os.environ.get("HONCHO_MONITOR_SPARK_CHAT_BASE", "http://192.168.10.211:8001")


@dataclass(frozen=True)
class HonchoSnapshot:
    services: dict[str, bool]
    pipeline: dict[str, dict[str, str]]
    db: dict[str, Any]
    queue: dict[str, int]
    queue_by_type: dict[str, dict[str, int]]
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
    doc_dims = snapshot.db.get("documents_dims")
    msg_dims = snapshot.db.get("messages_dims")
    db_dims_consistent = (
        doc_dims not in (None, "", 0, "0")
        and msg_dims not in (None, "", 0, "0")
        and str(doc_dims) == str(msg_dims)
    )
    if not embed_model or not embed_url:
        alerts.append("Embedding config missing")
    elif "api.openai.com" in embed_url or embed_model == "text-embedding-3-small":
        alerts.append("Embedding config looks like OpenAI fallback")
    elif not vector_dims and not db_dims_consistent:
        alerts.append("Embedding vector dimensions missing from env")

    if vector_dims and doc_dims not in (None, "", 0, "0") and str(doc_dims) != vector_dims:
        alerts.append("Document embedding dims mismatch")
    if vector_dims and msg_dims not in (None, "", 0, "0") and str(msg_dims) != vector_dims:
        alerts.append("Message embedding dims mismatch")

    if int(snapshot.errors.get("save_representation", 0)) > 0 or int(snapshot.errors.get("four_oh_one", 0)) > 0:
        alerts.append("Recent representation save / 401 errors")

    spark = snapshot.spark_goat
    if not spark.get("ok"):
        alerts.append("spark-goat chat failed")
    elif spark.get("thinking"):
        alerts.append("spark-goat thinking still enabled")
    elif float(spark.get("latency_s", 0.0) or 0.0) > 5.0:
        alerts.append("spark-goat chat latency degraded")

    if previous_state:
        prev_rep_state = previous_state.get("queue_by_type", {}).get("representation")
        if prev_rep_state is not None:
            prev_rep_done = int(prev_rep_state.get("done", 0))
            rep_done = int(snapshot.queue_by_type.get("representation", {}).get("done", 0))
            prev_docs = int(previous_state.get("documents_total", snapshot.db.get("documents_total", 0)))
            docs_delta = max(0, snapshot.db.get("documents_total", 0) - prev_docs)
            rep_delta = max(0, rep_done - prev_rep_done)
            if rep_delta > docs_delta:
                alerts.append("Queue advancing faster than documents")

    return alerts


def _normalize_queue_by_type(raw: Any) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    if not isinstance(raw, dict):
        return result
    for task_type, counts in raw.items():
        if isinstance(counts, dict):
            result[task_type] = {
                "pending": int(counts.get("pending", 0)),
                "done": int(counts.get("done", 0)),
            }
    return result


def format_report(snapshot: dict[str, Any] | HonchoSnapshot, previous_state: dict[str, Any] | None = None, now: datetime | None = None) -> str:
    if isinstance(snapshot, dict):
        snapshot = HonchoSnapshot(
            services=dict(snapshot.get("services", {})),
            pipeline={k: dict(v) for k, v in snapshot.get("pipeline", {}).items()},
            db=dict(snapshot.get("db", {})),
            queue={k: int(v) for k, v in snapshot.get("queue", {}).items()},
            queue_by_type=_normalize_queue_by_type(snapshot.get("queue_by_type")),
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
    spark_detail = f" (model={spark.get('model', '?')})" if spark.get("model") else ""
    lines.append(f"{ '🟢' if spark.get('ok') else '🔴' } spark-goat chat: {_fmt_s(spark.get('latency_s'))}{spark_tag}{spark_detail}")

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
    ssh_cmd = ["ssh"]
    tbot_config = Path("/var/lib/tbot/hermes-cron-ssh/ssh_config")
    if tbot_config.exists():
        ssh_cmd.extend(["-F", str(tbot_config)])
    ssh_cmd.extend(
        [
            "-o",
            "ConnectTimeout=5",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            HONCHO_TARGET,
            command,
        ]
    )
    result = subprocess.run(
        ssh_cmd,
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


def parse_queue_raw(queue_raw: str) -> dict[str, int]:
    """Parse PostgreSQL boolean text output for queue processed counts.

    Handles both legacy 2-column rows ``processed|count`` and the current
    3-column per-task-type rows ``task_type|processed|count``.
    """
    pending = done = 0
    for row in queue_raw.splitlines():
        parts = row.split("|")
        if len(parts) < 2:
            continue
        if len(parts) >= 3:
            # task_type|processed|count
            state, count = parts[1], parts[2]
        else:
            state, count = parts[0], parts[1]
        state = state.strip().lower()
        if state in ("f", "false"):
            pending += _int_or_zero(count)
        elif state in ("t", "true"):
            done += _int_or_zero(count)
    return {"pending": pending, "done": done}


def parse_queue_by_type_raw(queue_raw: str) -> dict[str, dict[str, int]]:
    """Parse per-task-type queue counts from PostgreSQL boolean text output.

    Expected rows: ``task_type|processed|count``. Returns a dict keyed by
    task_type with ``pending`` and ``done`` totals. Unknown task types are
    still preserved so the report stays truthful about queue composition.
    """
    by_type: dict[str, dict[str, int]] = {}
    for row in queue_raw.splitlines():
        parts = row.split("|")
        if len(parts) < 3:
            continue
        task_type, processed, count = parts[0], parts[1].strip().lower(), parts[2]
        bucket = by_type.setdefault(task_type, {"pending": 0, "done": 0})
        if processed in ("f", "false"):
            bucket["pending"] += _int_or_zero(count)
        elif processed in ("t", "true"):
            bucket["done"] += _int_or_zero(count)
    return by_type


def is_spark_goat_chat_url(url: str | None) -> bool:
    """Return True when ``url`` points at the spark-goat chat service."""
    if not url:
        return False
    return short_host(url) == "spark-goat" and ":8001" in url


def select_spark_model(pipeline: dict[str, dict[str, str]]) -> str:
    """Return the model name that should be used for the spark-goat chat smoke.

    If the pipeline's deriver base_url is on the spark-goat chat host, use its
    model; otherwise fall back to aeon-ultimate, which is known to be hosted on
    spark-goat. This prevents using a model routed elsewhere (e.g. mac-studio)
    against the spark-goat endpoint. Accept both current LAN and historical
    Tailscale addresses as spark-goat identity, even when the actual smoke
    probe uses the configured current endpoint.
    """
    deriver = pipeline.get("deriver", {})
    deriver_base = deriver.get("base_url", "")
    if is_spark_goat_chat_url(deriver_base):
        return deriver.get("model") or "aeon-ultimate"
    return "aeon-ultimate"


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
        "SELECT task_type, processed::text, count(*)::text FROM queue "
        "GROUP BY task_type, processed ORDER BY task_type, processed;\"",
        timeout=20,
    )
    queue_counts = parse_queue_raw(queue_raw)
    queue_by_type = parse_queue_by_type_raw(queue_raw)
    pending = queue_counts["pending"]
    done = queue_counts["done"]

    errors_raw = ssh(
        "docker logs honcho-deriver-1 --since 15m > /tmp/honcho-deriver-monitor.log 2>&1; "
        "printf 'save|%s\n' \"$(grep -c 'Failed to save representation' /tmp/honcho-deriver-monitor.log || true)\"; "
        "printf '401|%s\n' \"$(grep -c '401' /tmp/honcho-deriver-monitor.log || true)\"",
        timeout=30,
    )
    error_counts = _parse_kv_lines(errors_raw)

    # Pick a model actually served by the spark-goat chat stage (port 8001).
    # If the loaded pipeline routes the deriver elsewhere, fall back to the
    # aeon-ultimate model known to be hosted on spark-goat.
    spark_model = select_spark_model(pipeline)

    spark_ok, spark_data, spark_dt = curl_post_json(
        f"{SPARK_CHAT_BASE}/v1/chat/completions",
        {
            "model": spark_model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 20,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=30,
    )
    thinking = False
    spark_http_ok = spark_ok
    spark_model_ok = False
    if spark_data:
        msg = (spark_data.get("choices") or [{}])[0].get("message", {})
        thinking = bool(msg.get("reasoning_content"))
        spark_model_ok = bool(msg.get("content"))
        spark_ok = spark_ok and spark_model_ok
    elif spark_ok:
        # HTTP succeeded but body wasn't JSON / no choices -> treat as failure
        spark_ok = False

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
        queue_by_type=queue_by_type,
        errors={"save_representation": _int_or_zero(error_counts.get("save", "0")), "four_oh_one": _int_or_zero(error_counts.get("401", "0"))},
        spark_goat={
            "ok": spark_ok,
            "latency_s": spark_dt,
            "thinking": thinking,
            "model": spark_model,
            "http_ok": spark_http_ok,
            "model_ok": spark_model_ok,
        },
        deriver={"runs_15m": runs_15m, "last_duration_s": last_duration_s, "conclusions": conclusions},
    )

    current_state = {
        "queue_done": done,
        "queue_by_type": queue_by_type,
        "documents_total": db.get("documents_total", 0),
        "messages_total": db.get("messages_total", 0),
    }

    return snapshot, current_state


def main() -> int:
    snapshot, current_state = collect_snapshot()
    previous_state = load_state(STATE_PATH)
    report = format_report(snapshot, previous_state=previous_state)
    print(report)
    save_state(STATE_PATH, current_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
