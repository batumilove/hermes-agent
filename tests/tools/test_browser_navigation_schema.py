from __future__ import annotations

import model_tools


def _browser_description(toolsets: list[str]) -> str:
    model_tools._clear_tool_defs_cache()
    tools = model_tools.get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True)
    return next(
        tool["function"]["description"]
        for tool in tools
        if tool["function"]["name"] == "browser_navigate"
    )


def test_browser_navigate_static_description_avoids_unavailable_tool_names():
    desc = _browser_description(["browser"])

    assert "prefer available lightweight text retrieval tools first" in desc
    assert "curl via the terminal tool" not in desc
    assert "web_extract" not in desc
    assert "mcporter_call" not in desc


def test_browser_navigate_mentions_terminal_only_when_available():
    desc = _browser_description(["browser", "terminal"])

    assert "curl via the terminal tool" in desc
    assert "web_extract" not in desc
    assert "mcporter_call" not in desc


def test_browser_navigate_mentions_mcporter_only_when_available():
    desc = _browser_description(["browser", "mcporter"])

    assert "mcporter_call when a configured mcporter retrieval tool" in desc
    assert "curl via the terminal tool" not in desc
