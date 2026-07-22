"""Integration seam tests for action realization in model_tools."""

from __future__ import annotations

import json

import pytest

import model_tools


def test_handle_function_call_applies_action_realization_before_registry_dispatch(monkeypatch):
    seen = {}

    def fake_dispatch(name, args, **kwargs):
        seen["name"] = name
        seen["args"] = args
        return json.dumps({"ok": True, "args": args})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    monkeypatch.setattr(model_tools, "_emit_post_tool_call_hook", lambda **kwargs: None)
    monkeypatch.setattr("hermes_cli.middleware.run_tool_execution_middleware", lambda name, args, dispatch, **kw: dispatch(args))

    result = model_tools.handle_function_call(
        "cronjob",
        {"action": "remove", "job_id": "job-1"},
        user_task="Stop the daily backup cron",
    )

    assert json.loads(result)["ok"] is True
    assert seen["name"] == "cronjob"
    assert seen["args"]["action"] == "pause"


def test_handle_function_call_rejects_cron_remove_on_generic_continue(monkeypatch):
    called = False

    def fake_dispatch(name, args, **kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    monkeypatch.setattr(model_tools, "_emit_post_tool_call_hook", lambda **kwargs: None)

    result = model_tools.handle_function_call(
        "cronjob",
        {"action": "remove", "job_id": "job-1"},
        user_task="Continue",
    )

    payload = json.loads(result)
    assert called is False
    assert payload["action_realization"]["action"] == "reject"
    assert "current user turn" in payload["error"]


@pytest.mark.parametrize(
    "user_task",
    [
        "Continue, but do not delete the cron job.",
        "Can you explain how to delete a cron job?",
        "The prior recommendation said 'delete the cron'; continue with safe checks only.",
        "Delete old log files; continue with safe checks only.",
    ],
)
def test_handle_function_call_rejects_negated_hypothetical_or_quoted_cron_delete(
    monkeypatch, user_task
):
    called = False

    def fake_dispatch(name, args, **kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    monkeypatch.setattr(model_tools, "_emit_post_tool_call_hook", lambda **kwargs: None)

    result = model_tools.handle_function_call(
        "cronjob",
        {"action": "remove", "job_id": "job-1"},
        user_task=user_task,
    )

    payload = json.loads(result)
    assert called is False
    assert payload["action_realization"]["action"] == "reject"


def test_handle_function_call_allows_explicit_current_turn_cron_delete(monkeypatch):
    seen = {}

    def fake_dispatch(name, args, **kwargs):
        seen["name"] = name
        seen["args"] = args
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    monkeypatch.setattr(model_tools, "_emit_post_tool_call_hook", lambda **kwargs: None)
    monkeypatch.setattr(
        "hermes_cli.middleware.run_tool_execution_middleware",
        lambda name, args, dispatch, **kw: dispatch(args),
    )

    result = model_tools.handle_function_call(
        "cronjob",
        {"action": "remove", "job_id": "job-1"},
        user_task="Delete the daily backup cron job",
    )

    assert json.loads(result)["ok"] is True
    assert seen["args"]["action"] == "remove"


def test_handle_function_call_rejects_impossible_memory_before_dispatch(monkeypatch):
    called = False

    def fake_dispatch(name, args, **kwargs):
        nonlocal called
        called = True
        return json.dumps({"ok": True})

    monkeypatch.setattr(model_tools.registry, "dispatch", fake_dispatch)
    monkeypatch.setattr(model_tools, "_emit_post_tool_call_hook", lambda **kwargs: None)

    result = model_tools.handle_function_call(
        "memory",
        {"action": "add", "target": "memory", "content": "Fixed issue #7 and submitted PR #8"},
    )

    payload = json.loads(result)
    assert called is False
    assert payload["error"].startswith("Action realization rejected")
    assert payload["action_realization"]["action"] == "reject"
