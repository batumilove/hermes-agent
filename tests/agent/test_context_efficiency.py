import json
from pathlib import Path

from agent.context_efficiency import is_context_route_tool, normalize_config, record_tool_route, resolve_log_path
from hermes_cli.config import DEFAULT_CONFIG


class DummyAgent:
    session_id = "sess-1"
    platform = "telegram"
    model = "gpt-test"
    provider = "test-provider"
    _context_efficiency_config = {"enabled": False}


def test_default_config_disables_context_efficiency_canary():
    cfg = DEFAULT_CONFIG["context_efficiency"]
    assert cfg["enabled"] is False
    assert "session_search" in cfg["routes"]
    assert "memory_*" in cfg["routes"]
    assert "lcm_expand" in cfg["routes"]
    assert cfg["log_path"] == "logs/context_efficiency.jsonl"


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
    assert "memory routing" in event["arg_preview"]


def test_record_tool_route_writes_provider_memory_tool_with_wildcard_route(tmp_path):
    agent = DummyAgent()
    agent._context_efficiency_config = normalize_config({
        "enabled": True,
        "routes": ["memory_*"],
        "log_path": str(tmp_path / "frontier.jsonl"),
    })

    record_tool_route(agent, "memory_tencentdb_memory_search", {"query": "canary"}, {"ok": True}, 0.5)

    event = json.loads((tmp_path / "frontier.jsonl").read_text(encoding="utf-8"))
    assert event["route"] == "memory_tencentdb_memory_search"
    assert event["is_error"] is False


def test_record_tool_route_ignores_unlisted_routes(tmp_path):
    agent = DummyAgent()
    agent._context_efficiency_config = normalize_config({
        "enabled": True,
        "routes": ["session_search"],
        "log_path": str(tmp_path / "frontier.jsonl"),
    })

    record_tool_route(agent, "terminal", {"command": "date"}, "ok", 1.0)

    assert not Path(tmp_path / "frontier.jsonl").exists()
