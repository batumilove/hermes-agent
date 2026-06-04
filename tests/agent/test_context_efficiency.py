import json
from pathlib import Path

from agent.context_efficiency import (
    advise_context_route,
    is_context_route_tool,
    normalize_config,
    record_tool_route,
    resolve_log_path,
    route_family,
)
from hermes_cli.config import DEFAULT_CONFIG


class DummyAgent:
    session_id = "sess-1"
    platform = "telegram"
    model = "gpt-test"
    provider = "test-provider"
    _context_efficiency_user_message = ""
    _context_efficiency_config = {"enabled": False}


def test_default_config_disables_context_efficiency_canary():
    cfg = DEFAULT_CONFIG["context_efficiency"]
    assert cfg["enabled"] is False
    assert "session_search" in cfg["routes"]
    assert "memory_*" in cfg["routes"]
    assert "lcm_expand" in cfg["routes"]
    assert "lcm_status" in cfg["routes"]
    assert cfg["log_path"] == "logs/context_efficiency.jsonl"
    assert cfg["advisor"]["enabled"] is True


def test_route_advisor_classifies_common_context_sources():
    assert advise_context_route("what did we decide in the past session?")["family"] == "session_search"
    assert advise_context_route("remember the Canary Raven owner fact")["family"] == "durable_memory"
    assert advise_context_route("check current session LCM summary")["family"] == "current_session_lcm"
    assert advise_context_route("search current Hermes docs URL")["family"] == "web"
    assert advise_context_route("Find the current Hermes Agent configuration docs URL")["family"] == "web"
    assert advise_context_route("check local repo docs for context efficiency telemetry")["family"] == "file"
    assert advise_context_route("find the source file path for this code")["family"] == "file"
    assert advise_context_route("hello")["family"] == "unknown"


def test_route_family_groups_provider_and_lcm_routes():
    assert route_family("memory_tencentdb_memory_search") == "durable_memory"
    assert route_family("lcm_grep") == "current_session_lcm"
    assert route_family("lcm_status") == "current_session_lcm"
    assert route_family("web_search") == "web"
    assert route_family("search_files") == "file"
    assert route_family("terminal") == "other"


def test_context_route_tool_supports_provider_memory_prefix():
    assert is_context_route_tool("memory_tencentdb_memory_search")
    assert is_context_route_tool("memory_tencentdb_conversation_search")
    assert not is_context_route_tool("terminal")
    assert is_context_route_tool("custom_memory_search", ["custom_*"])


def test_normalize_config_accepts_route_csv_and_safe_limits():
    cfg = normalize_config({
        "enabled": True,
        "routes": "session_search, lcm_expand",
        "max_arg_chars": "12",
        "max_result_chars": "bad",
    })
    assert cfg["enabled"] is True
    assert cfg["routes"] == {"session_search", "lcm_expand"}
    assert cfg["max_arg_chars"] == 12
    assert cfg["max_result_chars"] == 500


def test_record_tool_route_writes_jsonl_when_enabled(tmp_path):
    agent = DummyAgent()
    agent._context_efficiency_config = normalize_config({
        "enabled": True,
        "routes": ["session_search"],
        "log_path": str(tmp_path / "frontier.jsonl"),
        "max_arg_chars": 200,
        "max_result_chars": 200,
    })

    record_tool_route(agent, "session_search", {"query": "memory routing"}, "{\"success\": true}", 0.123)

    path = resolve_log_path(agent._context_efficiency_config)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["route"] == "session_search"
    assert event["session_id"] == "sess-1"
    assert event["platform"] == "telegram"
    assert event["duration_s"] == 0.123
    assert event["is_error"] is False
    assert event["result_chars"] == len("{\"success\": true}")
    assert event["route_family"] == "session_search"
    assert event["advisor_family"] == "unknown"
    assert event["advisor_match"] is True
    assert "memory routing" in event["arg_preview"]


def test_record_tool_route_writes_provider_memory_tool_with_wildcard_route(tmp_path):
    agent = DummyAgent()
    agent._context_efficiency_user_message = "remember the Canary Raven owner fact"
    agent._context_efficiency_config = normalize_config({
        "enabled": True,
        "routes": ["memory_*"],
        "log_path": str(tmp_path / "frontier.jsonl"),
    })

    record_tool_route(agent, "memory_tencentdb_memory_search", {"query": "canary"}, {"ok": True}, 0.5)

    event = json.loads((tmp_path / "frontier.jsonl").read_text(encoding="utf-8"))
    assert event["route"] == "memory_tencentdb_memory_search"
    assert event["is_error"] is False
    assert event["route_family"] == "durable_memory"
    assert event["advisor_family"] == "durable_memory"
    assert event["advisor_match"] is True
    assert event["advisor_routes"] == ["memory_*", "honcho_search", "memory"]


def test_record_tool_route_logs_advisor_mismatch_without_blocking(tmp_path):
    agent = DummyAgent()
    agent._context_efficiency_user_message = "search current Hermes docs URL"
    agent._context_efficiency_config = normalize_config({
        "enabled": True,
        "routes": ["session_search"],
        "log_path": str(tmp_path / "frontier.jsonl"),
    })

    record_tool_route(agent, "session_search", {"query": "docs"}, "{}", 0.1)

    event = json.loads((tmp_path / "frontier.jsonl").read_text(encoding="utf-8"))
    assert event["route_family"] == "session_search"
    assert event["advisor_family"] == "web"
    assert event["advisor_match"] is False


def test_record_tool_route_ignores_unlisted_routes(tmp_path):
    agent = DummyAgent()
    agent._context_efficiency_config = normalize_config({
        "enabled": True,
        "routes": ["session_search"],
        "log_path": str(tmp_path / "frontier.jsonl"),
    })

    record_tool_route(agent, "terminal", {"command": "date"}, "ok", 1.0)

    assert not Path(tmp_path / "frontier.jsonl").exists()
