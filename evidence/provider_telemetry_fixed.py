"""Native Prometheus textfile telemetry for Hermes provider routing.

Observer-only plugin. Emits counters/gauges at the existing lifecycle hooks
(pre/post API request, classified API errors) plus the fallback/primary
observer hooks. Metrics are written under ``~/.hermes/state/prometheus`` and
served by the existing hermes-prometheus-textfile server on :9104.

Counters are reloaded from the existing ``.prom`` file on startup so gateway
restarts do not zero request/failure/fallback series.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

try:
    import fcntl
except ImportError:  # pragma: no cover - deployed integration is Linux-only
    fcntl = None

_METRICS_LOCK = threading.Lock()
_WRITE_LOCK = threading.Lock()
_PROCESS_LOCK_FILE: Any | None = None
_PROCESS_LOCK_PID: int | None = None
_LAST_REASON_BY_SESSION: dict[str, str] = {}
_INFLIGHT: dict[str, dict[str, Any]] = {}
_ACTIVE_FALLBACKS: dict[str, tuple[str, str, str, str, str]] = {}
_LOADED_FROM_DISK = False
_METRICS: dict[str, Any] = {
    "requests": {},  # (provider, model, outcome) -> int
    "failures": {},  # (provider, model, reason) -> int
    "fallbacks": {},  # (from_provider, from_model, to_provider, to_model, reason) -> int
    "fallback_recoveries": {},  # same labels as fallbacks
    "chain_exhaustions": {},  # (provider, model, primary_provider, primary_model, reason)
    "primary_restores": {},  # (provider, model) -> int
    "duration_sum": {},  # (provider, model, outcome) -> float
    "duration_count": {},  # (provider, model, outcome) -> int
    "approx_input_tokens": {},  # (provider, model) -> int
    "last_fallback_ts": {},  # (from_provider, from_model, to_provider, to_model) -> float
    "last_event_timestamp": 0.0,
}

_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')
_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|NaN|Inf|\+Inf|-Inf)\s*$"
)


def reset_metrics_for_tests() -> None:
    """Reset in-memory counters (tests only)."""
    global _LOADED_FROM_DISK, _PROCESS_LOCK_FILE, _PROCESS_LOCK_PID
    if _PROCESS_LOCK_FILE is not None:
        try:
            if fcntl is not None:
                fcntl.flock(_PROCESS_LOCK_FILE.fileno(), fcntl.LOCK_UN)
        finally:
            _PROCESS_LOCK_FILE.close()
            _PROCESS_LOCK_FILE = None
            _PROCESS_LOCK_PID = None
    with _METRICS_LOCK:
        for key in (
            "requests",
            "failures",
            "fallbacks",
            "fallback_recoveries",
            "chain_exhaustions",
            "primary_restores",
            "duration_sum",
            "duration_count",
            "approx_input_tokens",
            "last_fallback_ts",
        ):
            _METRICS[key] = {}
        _METRICS["last_event_timestamp"] = 0.0
        _LAST_REASON_BY_SESSION.clear()
        _INFLIGHT.clear()
        _ACTIVE_FALLBACKS.clear()
        _LOADED_FROM_DISK = False


def register(ctx: Any) -> None:
    if not getattr(ctx, "can_claim_provider_telemetry_writer", True):
        _warn("provider telemetry writer ownership skipped on passive surface")
        return
    if not _claim_writer_ownership():
        _warn("provider telemetry writer ownership unavailable; hooks not registered")
        return
    _ensure_loaded_from_disk()
    ctx.register_hook("pre_api_request", on_pre_api_request)
    ctx.register_hook("post_api_request", on_post_api_request)
    ctx.register_hook("api_request_error", on_api_request_error)
    ctx.register_hook("on_fallback_activated", on_fallback_activated)
    ctx.register_hook("on_fallback_chain_exhausted", on_fallback_chain_exhausted)
    ctx.register_hook("on_primary_restored", on_primary_restored)
    _write_metrics()


def _claim_writer_ownership() -> bool:
    """Elect one process to own the cumulative metrics file."""
    global _PROCESS_LOCK_FILE, _PROCESS_LOCK_PID

    current_pid = os.getpid()
    if _PROCESS_LOCK_FILE is not None and _PROCESS_LOCK_PID == current_pid:
        return True
    if _PROCESS_LOCK_FILE is not None:
        # A forked child must not inherit the parent's ownership claim.
        _PROCESS_LOCK_FILE.close()
        _PROCESS_LOCK_FILE = None
        _PROCESS_LOCK_PID = None
    if fcntl is None:
        _warn("fcntl is unavailable; refusing unsafe multi-process telemetry writes")
        return False

    path = _metrics_file()
    lock_file = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(path.suffix + ".lock")
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        lock_file = os.fdopen(fd, "r+", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{current_pid}\n")
        lock_file.flush()
        _PROCESS_LOCK_FILE = lock_file
        _PROCESS_LOCK_PID = current_pid
        return True
    except Exception as exc:
        if lock_file is not None and not lock_file.closed:
            lock_file.close()
        _warn(f"could not acquire provider telemetry writer lock: {exc}")
        return False


def on_pre_api_request(**kwargs: Any) -> None:
    try:
        _ensure_loaded_from_disk()
        provider = _label(kwargs.get("provider"))
        model = _label(kwargs.get("model"))
        api_request_id = str(kwargs.get("api_request_id") or "")
        tokens = _as_int(kwargs.get("approx_input_tokens"))
        if provider != "unknown" and model != "unknown" and tokens is not None:
            with _METRICS_LOCK:
                _METRICS["approx_input_tokens"][(provider, model)] = tokens
                _METRICS["last_event_timestamp"] = time.time()
        if api_request_id:
            with _METRICS_LOCK:
                _INFLIGHT[api_request_id] = {
                    "provider": provider,
                    "model": model,
                    "started_at": kwargs.get("started_at") or time.time(),
                    "approx_input_tokens": tokens,
                }
        _write_metrics()
    except Exception as exc:  # pragma: no cover - fail-open
        _warn(f"pre_api_request failed: {exc}")


def on_post_api_request(**kwargs: Any) -> None:
    try:
        _ensure_loaded_from_disk()
        provider = _label(kwargs.get("provider"))
        model = _label(kwargs.get("model"))
        duration = _as_float(kwargs.get("api_duration"))
        if duration is None:
            started = _as_float(kwargs.get("started_at"))
            ended = _as_float(kwargs.get("ended_at"))
            if started is not None and ended is not None and ended >= started:
                duration = ended - started
        api_request_id = str(kwargs.get("api_request_id") or "")
        session_id = str(kwargs.get("session_id") or "")
        with _METRICS_LOCK:
            if api_request_id and api_request_id in _INFLIGHT:
                inflight = _INFLIGHT.pop(api_request_id)
                provider = provider if provider != "unknown" else inflight.get("provider", provider)
                model = model if model != "unknown" else inflight.get("model", model)
                if duration is None:
                    started = _as_float(inflight.get("started_at"))
                    if started is not None:
                        duration = max(0.0, time.time() - started)
            _inc_locked(_METRICS["requests"], (provider, model, "success"), 1)
            active = _ACTIVE_FALLBACKS.get(session_id) if session_id else None
            if active and (provider, model) == (active[2], active[3]):
                _inc_locked(_METRICS["fallback_recoveries"], active, 1)
                _ACTIVE_FALLBACKS.pop(session_id, None)
            if duration is not None:
                key = (provider, model, "success")
                _METRICS["duration_sum"][key] = _METRICS["duration_sum"].get(key, 0.0) + float(duration)
                _METRICS["duration_count"][key] = _METRICS["duration_count"].get(key, 0) + 1
            _METRICS["last_event_timestamp"] = time.time()
        _write_metrics()
    except Exception as exc:  # pragma: no cover - fail-open
        _warn(f"post_api_request failed: {exc}")


def on_api_request_error(**kwargs: Any) -> None:
    try:
        _ensure_loaded_from_disk()
        provider = _label(kwargs.get("provider"))
        model = _label(kwargs.get("model"))
        reason = _label(kwargs.get("reason") or "unknown")
        session_id = str(kwargs.get("session_id") or "")
        duration = _as_float(kwargs.get("api_duration"))
        tokens = _as_int(kwargs.get("approx_input_tokens"))
        api_request_id = str(kwargs.get("api_request_id") or "")

        with _METRICS_LOCK:
            if api_request_id and api_request_id in _INFLIGHT:
                inflight = _INFLIGHT.pop(api_request_id)
                provider = provider if provider != "unknown" else inflight.get("provider", provider)
                model = model if model != "unknown" else inflight.get("model", model)
                if tokens is None:
                    tokens = _as_int(inflight.get("approx_input_tokens"))
                if duration is None:
                    started = _as_float(inflight.get("started_at"))
                    if started is not None:
                        duration = max(0.0, time.time() - started)
            _inc_locked(_METRICS["requests"], (provider, model, "error"), 1)
            _inc_locked(_METRICS["failures"], (provider, model, reason), 1)
            if duration is not None:
                key = (provider, model, "error")
                _METRICS["duration_sum"][key] = _METRICS["duration_sum"].get(key, 0.0) + float(duration)
                _METRICS["duration_count"][key] = _METRICS["duration_count"].get(key, 0) + 1
            if tokens is not None:
                _METRICS["approx_input_tokens"][(provider, model)] = tokens
            if session_id:
                _LAST_REASON_BY_SESSION[session_id] = reason
            # Keep map bounded.
            if len(_LAST_REASON_BY_SESSION) > 512:
                for old in list(_LAST_REASON_BY_SESSION.keys())[:64]:
                    _LAST_REASON_BY_SESSION.pop(old, None)
            _METRICS["last_event_timestamp"] = time.time()
        _write_metrics()
    except Exception as exc:  # pragma: no cover - fail-open
        _warn(f"api_request_error failed: {exc}")


def on_fallback_activated(**kwargs: Any) -> None:
    try:
        _ensure_loaded_from_disk()
        from_provider = _label(kwargs.get("from_provider") or kwargs.get("old_provider"))
        from_model = _label(kwargs.get("from_model") or kwargs.get("old_model"))
        to_provider = _label(kwargs.get("to_provider") or kwargs.get("provider"))
        to_model = _label(kwargs.get("to_model") or kwargs.get("model"))
        reason = _label(kwargs.get("reason") or "")
        session_id = str(kwargs.get("session_id") or "")
        if reason in {"", "unknown"} and session_id:
            with _METRICS_LOCK:
                reason = _LAST_REASON_BY_SESSION.get(session_id, "unknown")
        if reason in {"", "unknown"}:
            reason = "unknown"
        with _METRICS_LOCK:
            key = (from_provider, from_model, to_provider, to_model, reason)
            _inc_locked(_METRICS["fallbacks"], key, 1)
            if session_id:
                _ACTIVE_FALLBACKS[session_id] = key
            _METRICS["last_fallback_ts"][(from_provider, from_model, to_provider, to_model)] = time.time()
            _METRICS["last_event_timestamp"] = time.time()
        _write_metrics()
    except Exception as exc:  # pragma: no cover - fail-open
        _warn(f"on_fallback_activated failed: {exc}")


def on_fallback_chain_exhausted(**kwargs: Any) -> None:
    try:
        _ensure_loaded_from_disk()
        provider = _label(kwargs.get("provider"))
        model = _label(kwargs.get("model"))
        primary_provider = _label(kwargs.get("primary_provider"))
        primary_model = _label(kwargs.get("primary_model"))
        reason = _label(kwargs.get("reason") or "unknown")
        session_id = str(kwargs.get("session_id") or "")
        with _METRICS_LOCK:
            key = (provider, model, primary_provider, primary_model, reason)
            _inc_locked(_METRICS["chain_exhaustions"], key, 1)
            if session_id:
                _ACTIVE_FALLBACKS.pop(session_id, None)
            _METRICS["last_event_timestamp"] = time.time()
        _write_metrics()
    except Exception as exc:  # pragma: no cover - fail-open
        _warn(f"on_fallback_chain_exhausted failed: {exc}")


def on_primary_restored(**kwargs: Any) -> None:
    try:
        _ensure_loaded_from_disk()
        provider = _label(kwargs.get("provider") or kwargs.get("to_provider"))
        model = _label(kwargs.get("model") or kwargs.get("to_model"))
        session_id = str(kwargs.get("session_id") or "")
        with _METRICS_LOCK:
            _inc_locked(_METRICS["primary_restores"], (provider, model), 1)
            if session_id:
                _ACTIVE_FALLBACKS.pop(session_id, None)
            _METRICS["last_event_timestamp"] = time.time()
        _write_metrics()
    except Exception as exc:  # pragma: no cover - fail-open
        _warn(f"on_primary_restored failed: {exc}")


def _metrics_file() -> Path:
    override = os.environ.get("HERMES_PROVIDER_TELEMETRY_METRICS_FILE", "").strip()
    if override:
        path = Path(override).expanduser()
        if path.name.endswith(".prom") and (not path.exists() or path.is_file()):
            return path
        _warn("ignoring HERMES_PROVIDER_TELEMETRY_METRICS_FILE override that is not a .prom regular file")
    return get_hermes_home() / "state" / "prometheus" / "hermes_provider_telemetry.prom"


def _ensure_loaded_from_disk() -> None:
    global _LOADED_FROM_DISK
    with _METRICS_LOCK:
        if _LOADED_FROM_DISK:
            return
        _load_metrics_from_file_locked()
        _LOADED_FROM_DISK = True


def _load_metrics_from_file_locked() -> None:
    """Hydrate in-memory counters from the existing textfile (best-effort)."""
    path = _metrics_file()
    try:
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
    except Exception:
        return

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        labels = _parse_labels(m.group("labels") or "")
        value_s = m.group("value")
        try:
            if value_s in {"NaN", "Inf", "+Inf", "-Inf"}:
                continue
            value = float(value_s)
        except ValueError:
            continue

        if name == "hermes_provider_telemetry_last_event_timestamp_seconds":
            _METRICS["last_event_timestamp"] = max(float(_METRICS["last_event_timestamp"] or 0.0), value)
            continue
        if name == "hermes_model_requests_total":
            key = (labels.get("provider", "unknown"), labels.get("model", "unknown"), labels.get("outcome", "unknown"))
            _METRICS["requests"][key] = int(value)
        elif name == "hermes_model_failures_total":
            key = (labels.get("provider", "unknown"), labels.get("model", "unknown"), labels.get("reason", "unknown"))
            _METRICS["failures"][key] = int(value)
        elif name == "hermes_model_fallbacks_total":
            key = (
                labels.get("from_provider", "unknown"),
                labels.get("from_model", "unknown"),
                labels.get("to_provider", "unknown"),
                labels.get("to_model", "unknown"),
                labels.get("reason", "unknown"),
            )
            _METRICS["fallbacks"][key] = int(value)
        elif name == "hermes_model_fallback_recoveries_total":
            key = (
                labels.get("from_provider", "unknown"),
                labels.get("from_model", "unknown"),
                labels.get("to_provider", "unknown"),
                labels.get("to_model", "unknown"),
                labels.get("reason", "unknown"),
            )
            _METRICS["fallback_recoveries"][key] = int(value)
        elif name == "hermes_model_fallback_chain_exhaustions_total":
            key = (
                labels.get("provider", "unknown"),
                labels.get("model", "unknown"),
                labels.get("primary_provider", "unknown"),
                labels.get("primary_model", "unknown"),
                labels.get("reason", "unknown"),
            )
            _METRICS["chain_exhaustions"][key] = int(value)
        elif name == "hermes_model_primary_restores_total":
            key = (labels.get("provider", "unknown"), labels.get("model", "unknown"))
            _METRICS["primary_restores"][key] = int(value)
        elif name == "hermes_model_request_duration_seconds_sum":
            key = (labels.get("provider", "unknown"), labels.get("model", "unknown"), labels.get("outcome", "unknown"))
            _METRICS["duration_sum"][key] = float(value)
        elif name == "hermes_model_request_duration_seconds_count":
            key = (labels.get("provider", "unknown"), labels.get("model", "unknown"), labels.get("outcome", "unknown"))
            _METRICS["duration_count"][key] = int(value)
        elif name == "hermes_model_approx_input_tokens":
            key = (labels.get("provider", "unknown"), labels.get("model", "unknown"))
            _METRICS["approx_input_tokens"][key] = int(value)
        elif name == "hermes_model_last_fallback_timestamp_seconds":
            key = (
                labels.get("from_provider", "unknown"),
                labels.get("from_model", "unknown"),
                labels.get("to_provider", "unknown"),
                labels.get("to_model", "unknown"),
            )
            _METRICS["last_fallback_ts"][key] = float(value)


def _parse_labels(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _LABEL_RE.finditer(raw):
        key = match.group(1)
        val = match.group(2)
        val = val.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        out[key] = val
    return out


def _write_metrics() -> None:
    # Serialize snapshot creation and publication. Without this outer lock, a
    # slower writer can publish an older snapshot after a newer one and make
    # Prometheus counters move backwards.
    with _WRITE_LOCK:
        _write_metrics_serialized()


def _write_metrics_serialized() -> None:
    try:
        path = _metrics_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _METRICS_LOCK:
            snapshot = {
                "requests": dict(_METRICS["requests"]),
                "failures": dict(_METRICS["failures"]),
                "fallbacks": dict(_METRICS["fallbacks"]),
                "fallback_recoveries": dict(_METRICS["fallback_recoveries"]),
                "chain_exhaustions": dict(_METRICS["chain_exhaustions"]),
                "primary_restores": dict(_METRICS["primary_restores"]),
                "duration_sum": dict(_METRICS["duration_sum"]),
                "duration_count": dict(_METRICS["duration_count"]),
                "approx_input_tokens": dict(_METRICS["approx_input_tokens"]),
                "last_fallback_ts": dict(_METRICS["last_fallback_ts"]),
                "last_event_timestamp": float(_METRICS["last_event_timestamp"] or 0.0),
            }

        lines = [
            "# HELP hermes_provider_telemetry_up 1 if the provider telemetry plugin is loaded and writing metrics.",
            "# TYPE hermes_provider_telemetry_up gauge",
            "hermes_provider_telemetry_up 1",
            "# HELP hermes_provider_telemetry_last_event_timestamp_seconds Unix timestamp of the last provider telemetry event.",
            "# TYPE hermes_provider_telemetry_last_event_timestamp_seconds gauge",
            f"hermes_provider_telemetry_last_event_timestamp_seconds {snapshot['last_event_timestamp']:.3f}",
            "# HELP hermes_model_requests_total Hermes model API requests by provider/model/outcome.",
            "# TYPE hermes_model_requests_total counter",
        ]
        for (provider, model, outcome), value in sorted(snapshot["requests"].items()):
            lines.append(
                f'hermes_model_requests_total{{provider="{_escape(provider)}",model="{_escape(model)}",outcome="{_escape(outcome)}"}} {value}'
            )

        lines.extend(
            [
                "# HELP hermes_model_failures_total Hermes model API failures by classified reason.",
                "# TYPE hermes_model_failures_total counter",
            ]
        )
        for (provider, model, reason), value in sorted(snapshot["failures"].items()):
            lines.append(
                f'hermes_model_failures_total{{provider="{_escape(provider)}",model="{_escape(model)}",reason="{_escape(reason)}"}} {value}'
            )

        lines.extend(
            [
                "# HELP hermes_model_fallbacks_total Hermes fallback activations with classified reason.",
                "# TYPE hermes_model_fallbacks_total counter",
            ]
        )
        for (fp, fm, tp, tm, reason), value in sorted(snapshot["fallbacks"].items()):
            lines.append(
                "hermes_model_fallbacks_total{"
                f'from_provider="{_escape(fp)}",from_model="{_escape(fm)}",'
                f'to_provider="{_escape(tp)}",to_model="{_escape(tm)}",'
                f'reason="{_escape(reason)}"'
                f"}} {value}"
            )

        lines.extend(
            [
                "# HELP hermes_model_fallback_recoveries_total Fallback activations that produced a successful response.",
                "# TYPE hermes_model_fallback_recoveries_total counter",
            ]
        )
        for (fp, fm, tp, tm, reason), value in sorted(snapshot["fallback_recoveries"].items()):
            lines.append(
                "hermes_model_fallback_recoveries_total{"
                f'from_provider="{_escape(fp)}",from_model="{_escape(fm)}",'
                f'to_provider="{_escape(tp)}",to_model="{_escape(tm)}",'
                f'reason="{_escape(reason)}"'
                f"}} {value}"
            )

        lines.extend(
            [
                "# HELP hermes_model_fallback_chain_exhaustions_total Fallback chains exhausted without a usable provider response.",
                "# TYPE hermes_model_fallback_chain_exhaustions_total counter",
            ]
        )
        for (provider, model, pp, pm, reason), value in sorted(snapshot["chain_exhaustions"].items()):
            lines.append(
                "hermes_model_fallback_chain_exhaustions_total{"
                f'provider="{_escape(provider)}",model="{_escape(model)}",'
                f'primary_provider="{_escape(pp)}",primary_model="{_escape(pm)}",'
                f'reason="{_escape(reason)}"'
                f"}} {value}"
            )

        lines.extend(
            [
                "# HELP hermes_model_primary_restores_total Hermes primary provider restorations.",
                "# TYPE hermes_model_primary_restores_total counter",
            ]
        )
        for (provider, model), value in sorted(snapshot["primary_restores"].items()):
            lines.append(
                f'hermes_model_primary_restores_total{{provider="{_escape(provider)}",model="{_escape(model)}"}} {value}'
            )

        lines.extend(
            [
                "# HELP hermes_model_request_duration_seconds_sum Hermes model API request duration seconds (sum).",
                "# TYPE hermes_model_request_duration_seconds_sum counter",
            ]
        )
        for (provider, model, outcome), value in sorted(snapshot["duration_sum"].items()):
            lines.append(
                f'hermes_model_request_duration_seconds_sum{{provider="{_escape(provider)}",model="{_escape(model)}",outcome="{_escape(outcome)}"}} {value:.6f}'
            )

        lines.extend(
            [
                "# HELP hermes_model_request_duration_seconds_count Hermes model API request duration observations.",
                "# TYPE hermes_model_request_duration_seconds_count counter",
            ]
        )
        for (provider, model, outcome), value in sorted(snapshot["duration_count"].items()):
            lines.append(
                f'hermes_model_request_duration_seconds_count{{provider="{_escape(provider)}",model="{_escape(model)}",outcome="{_escape(outcome)}"}} {value}'
            )

        lines.extend(
            [
                "# HELP hermes_model_approx_input_tokens Most recent approximate input token estimate for provider/model.",
                "# TYPE hermes_model_approx_input_tokens gauge",
            ]
        )
        for (provider, model), value in sorted(snapshot["approx_input_tokens"].items()):
            lines.append(
                f'hermes_model_approx_input_tokens{{provider="{_escape(provider)}",model="{_escape(model)}"}} {value}'
            )

        lines.extend(
            [
                "# HELP hermes_model_last_fallback_timestamp_seconds Unix timestamp of the latest fallback activation for a transition.",
                "# TYPE hermes_model_last_fallback_timestamp_seconds gauge",
            ]
        )
        for (fp, fm, tp, tm), value in sorted(snapshot["last_fallback_ts"].items()):
            lines.append(
                "hermes_model_last_fallback_timestamp_seconds{"
                f'from_provider="{_escape(fp)}",from_model="{_escape(fm)}",'
                f'to_provider="{_escape(tp)}",to_model="{_escape(tm)}"'
                f"}} {float(value):.3f}"
            )

        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        # Metrics must never interfere with agent execution.
        return


def _inc_locked(mapping: dict, key: tuple, amount: int = 1) -> None:
    mapping[key] = mapping.get(key, 0) + amount


def _label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    # Keep cardinality bounded and Prometheus-label safe.
    cleaned = []
    for ch in text[:96]:
        if ch.isalnum() or ch in {".", "-", "_", ":", "/", "+"}:
            cleaned.append(ch)
        else:
            cleaned.append("_")
    return "".join(cleaned) or "unknown"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _warn(message: str) -> None:
    print(f"provider_telemetry: {message}", file=sys.stderr)
