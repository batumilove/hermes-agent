"""Canary telemetry for context-route cost/performance analysis.

This module intentionally does not change routing.  It records context-related
route choices so operators can build an empirical Efficiency Frontier for their
own deployment before enabling adaptive policies.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

CONTEXT_ROUTE_TOOLS = frozenset(
    {
        "session_search",
        "memory",
        "honcho_profile",
        "honcho_search",
        "honcho_reasoning",
        "honcho_context",
        "honcho_conclude",
        "lcm_grep",
        "lcm_load_session",
        "lcm_describe",
        "lcm_expand",
        "lcm_expand_query",
        "web_search",
        "web_extract",
        "read_file",
        "search_files",
    }
)

DEFAULT_LOG_PATH = "logs/context_efficiency.jsonl"


def normalize_config(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return sanitized context-efficiency config with safe defaults."""
    raw = dict(config or {})
    routes = raw.get("routes") or CONTEXT_ROUTE_TOOLS
    if isinstance(routes, str):
        routes = [item.strip() for item in routes.split(",") if item.strip()]
    try:
        max_arg_chars = int(raw.get("max_arg_chars", 500))
    except Exception:
        max_arg_chars = 500
    try:
        max_result_chars = int(raw.get("max_result_chars", 500))
    except Exception:
        max_result_chars = 500
    return {
        "enabled": bool(raw.get("enabled", False)),
        "routes": frozenset(str(item) for item in routes),
        "log_path": str(raw.get("log_path") or DEFAULT_LOG_PATH),
        "max_arg_chars": max(0, max_arg_chars),
        "max_result_chars": max(0, max_result_chars),
    }


def resolve_log_path(config: Mapping[str, Any]) -> Path:
    path = Path(str(config.get("log_path") or DEFAULT_LOG_PATH)).expanduser()
    if not path.is_absolute():
        path = get_hermes_home() / path
    return path


def _truncate(value: Any, limit: int) -> Any:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    if limit <= 0:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def _stable_hash(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def record_tool_route(agent: Any, tool_name: str, args: Mapping[str, Any] | None, result: Any, duration: float, *, is_error: bool = False) -> None:
    """Append one context-route telemetry event if the canary is enabled.

    Errors here must never affect tool execution.
    """
    try:
        cfg = getattr(agent, "_context_efficiency_config", None) or {}
        if not cfg.get("enabled") or tool_name not in cfg.get("routes", CONTEXT_ROUTE_TOOLS):
            return
        result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        event = {
            "ts": time.time(),
            "session_id": getattr(agent, "session_id", "") or "",
            "platform": getattr(agent, "platform", "") or "",
            "model": getattr(agent, "model", "") or "",
            "provider": getattr(agent, "provider", "") or "",
            "route": tool_name,
            "duration_s": round(float(duration or 0.0), 3),
            "is_error": bool(is_error),
            "arg_hash": _stable_hash(args or {}),
            "result_hash": _stable_hash(result_text),
            "arg_preview": _truncate(args or {}, int(cfg.get("max_arg_chars", 500))),
            "result_chars": len(result_text),
            "result_preview": _truncate(result_text, int(cfg.get("max_result_chars", 500))),
        }
        path = resolve_log_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        logger.debug("context efficiency telemetry skipped: %s", exc, exc_info=True)
