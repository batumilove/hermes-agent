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
from typing import Any, Iterable, Mapping

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

CONTEXT_ROUTE_TOOLS = frozenset(
    {
        "session_search",
        "memory",
        "memory_*",
        "honcho_profile",
        "honcho_search",
        "honcho_reasoning",
        "honcho_context",
        "honcho_conclude",
        "lcm_status",
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

ROUTE_ADVISOR_FAMILIES: dict[str, tuple[str, ...]] = {
    "session_search": ("session_search",),
    "durable_memory": ("memory", "memory_*", "honcho_profile", "honcho_search", "honcho_reasoning", "honcho_context"),
    "current_session_lcm": ("lcm_status", "lcm_grep", "lcm_load_session", "lcm_describe", "lcm_expand", "lcm_expand_query"),
    "web": ("web_search", "web_extract"),
    "file": ("search_files", "read_file"),
}


def is_context_route_tool(tool_name: str, routes: Iterable[object] | None = None) -> bool:
    """Return whether ``tool_name`` should be logged as a context route.

    Besides exact route names, a route ending in ``*`` acts as a prefix match.
    This covers provider/plugin memory tools such as
    ``memory_tencentdb_memory_search`` without needing every provider-specific
    tool name in the global defaults.
    """
    route_set = routes or CONTEXT_ROUTE_TOOLS
    try:
        route_items = [str(item) for item in route_set]
    except Exception:
        route_items = list(CONTEXT_ROUTE_TOOLS)
    name = str(tool_name)
    if name in route_items:
        return True
    return any(item.endswith("*") and name.startswith(item[:-1]) for item in route_items)


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
    advisor = raw.get("advisor", {}) if isinstance(raw.get("advisor", {}), Mapping) else {}
    previews_enabled = bool(raw.get("previews_enabled", False)) and max_arg_chars > 0 and max_result_chars > 0
    return {
        "enabled": bool(raw.get("enabled", False)),
        "routes": frozenset(str(item) for item in routes),
        "log_path": str(raw.get("log_path") or DEFAULT_LOG_PATH),
        "max_arg_chars": max(0, max_arg_chars),
        "max_result_chars": max(0, max_result_chars),
        "previews_enabled": previews_enabled,
        "advisor_enabled": bool(advisor.get("enabled", raw.get("advisor_enabled", True))),
    }


def route_family(tool_name: str) -> str:
    """Return a coarse route family for advisor-vs-actual analysis."""
    for family, routes in ROUTE_ADVISOR_FAMILIES.items():
        if is_context_route_tool(tool_name, routes):
            return family
    return "other"


def advise_context_route(user_message: str | None) -> dict[str, Any]:
    """Heuristic, read-only route recommendation for context-source selection.

    The advisor is intentionally simple and observational. It does not change
    tool availability or execution; telemetry compares this recommendation with
    the route the model actually chose.
    """
    text = (user_message or "").strip()
    lower = text.lower()
    if not lower:
        return {"family": "unknown", "routes": [], "reason": "empty_prompt"}

    def has_any(*needles: str) -> bool:
        return any(needle in lower for needle in needles)

    if has_any("current session", "this session", "lcm", "compressed", "summary", "expand"):
        return {"family": "current_session_lcm", "routes": ["lcm_grep", "lcm_expand"], "reason": "current_session_or_lcm_keyword"}
    if has_any("past session", "previous session", "prior session", "session_search", "what did we", "where did we leave", "earlier conversation", "conversation history"):
        return {"family": "session_search", "routes": ["session_search"], "reason": "past_session_keyword"}
    if has_any("remember", "memory", "user preference", "durable", "long-term", "honcho", "profile", "canary owner", "owner fact"):
        return {"family": "durable_memory", "routes": ["memory_*", "honcho_search", "memory"], "reason": "durable_memory_keyword"}
    if has_any("local repo", "this repo", "repo", "file", "path", "source", "code", "read_file", "search_files", "line", "grep", "locate"):
        return {"family": "file", "routes": ["search_files", "read_file"], "reason": "repo_file_keyword"}
    if has_any("web", "internet", "current", "latest", "docs", "documentation", "github", "url", "website", "external", "news", "search for", "find"):
        return {"family": "web", "routes": ["web_search", "web_extract"], "reason": "external_or_current_keyword"}
    return {"family": "unknown", "routes": [], "reason": "no_heuristic_match"}


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
        if not cfg.get("enabled") or not is_context_route_tool(tool_name, cfg.get("routes", CONTEXT_ROUTE_TOOLS)):
            return
        result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        advisor = advise_context_route(getattr(agent, "_context_efficiency_user_message", "")) if cfg.get("advisor_enabled", True) else {"family": "disabled", "routes": [], "reason": "disabled"}
        actual_family = route_family(tool_name)
        recommended_family = str(advisor.get("family") or "unknown")
        recommended_routes = [str(item) for item in advisor.get("routes", [])]
        previews_enabled = bool(cfg.get("previews_enabled", False))
        event = {
            "ts": time.time(),
            "session_id": getattr(agent, "session_id", "") or "",
            "platform": getattr(agent, "platform", "") or "",
            "model": getattr(agent, "model", "") or "",
            "provider": getattr(agent, "provider", "") or "",
            "route": tool_name,
            "route_family": actual_family,
            "advisor_family": recommended_family,
            "advisor_routes": recommended_routes,
            "advisor_reason": str(advisor.get("reason") or ""),
            "advisor_match": recommended_family in ("unknown", "disabled") or recommended_family == actual_family,
            "duration_s": round(float(duration or 0.0), 3),
            "is_error": bool(is_error),
            "arg_hash": _stable_hash(args or {}),
            "result_hash": _stable_hash(result_text),
            "arg_preview": _truncate(args or {}, int(cfg.get("max_arg_chars", 500))) if previews_enabled else "",
            "result_chars": len(result_text),
            "result_preview": _truncate(result_text, int(cfg.get("max_result_chars", 500))) if previews_enabled else "",
        }
        path = resolve_log_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception as exc:
        logger.debug("context efficiency telemetry skipped: %s", exc, exc_info=True)
