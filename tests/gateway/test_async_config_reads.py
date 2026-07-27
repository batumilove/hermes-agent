"""Regression coverage for gateway config reads on async message paths."""

import ast
import inspect
import threading
from pathlib import Path

import pytest

import gateway.run as gateway_run


_GATEWAY_DIR = Path(gateway_run.__file__).parent
GATEWAY_ASYNC_SOURCES = tuple(
    (str(path.relative_to(_GATEWAY_DIR.parent)), path.read_text())
    for path in sorted(_GATEWAY_DIR.rglob("*.py"))
)


@pytest.mark.asyncio
async def test_load_gateway_config_async_runs_loader_off_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    loader_threads = []
    expected = {"display": {"show_reasoning": True}}

    def blocking_loader():
        loader_threads.append(threading.get_ident())
        return expected

    monkeypatch.setattr(gateway_run, "_load_gateway_config", blocking_loader)

    result = await gateway_run._load_gateway_config_async()

    assert result is expected
    assert len(loader_threads) == 1
    assert loader_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_session_runtime_without_snapshot_loads_config_off_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    loader_threads = []
    expected = {"model": {"default": "test/model"}}
    captured = {}

    def blocking_loader():
        loader_threads.append(threading.get_ident())
        return expected

    runner = object.__new__(gateway_run.GatewayRunner)
    runner._session_model_overrides = {}

    def resolve_runtime(**kwargs):
        captured.update(kwargs)
        return "test/model", {}

    runner._resolve_session_agent_runtime = resolve_runtime
    monkeypatch.setattr(gateway_run, "_load_gateway_config", blocking_loader)

    result = await runner._resolve_session_agent_runtime_async()

    assert result == ("test/model", {})
    assert captured["user_config"] is expected
    assert loader_threads and loader_threads[0] != event_loop_thread


def test_async_gateway_functions_never_call_sync_config_loader_directly():
    """A direct call can hold the config lock/deepcopy on the event loop."""
    offenders = []
    for module_name, source in GATEWAY_ASYNC_SOURCES:
        tree = ast.parse(source)
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_load_gateway_config"
            ):
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            if isinstance(parent, ast.AsyncFunctionDef):
                offenders.append((module_name, parent.name, node.lineno))

    assert offenders == []


def test_runtime_async_functions_do_not_call_sync_config_helpers_inline():
    """Known config-reading helpers must never execute on the runtime loop."""
    prohibited = {
        "load_config",
        "_goal_max_turns_from_config",
        "_read_user_config",
        "_load_background_notifications_mode",
        "_scale_to_zero_is_idle",
        "_scale_to_zero_idle_timeout_seconds",
        "_resolve_session_reasoning_config",
        "_resolve_session_service_tier",
        "_restart_loop_guard_config",
        "_schedule_resume_pending_sessions",
    }
    startup_only = {"start", "start_gateway"}
    offenders = []

    for module_name, source in GATEWAY_ASYNC_SOURCES:
        tree = ast.parse(source)
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            else:
                continue

            has_keyword = {kw.arg for kw in node.keywords}
            # These helpers skip config reads when their preloaded values are passed.
            if (
                call_name == "_scale_to_zero_is_idle"
                and "idle_timeout_seconds" in has_keyword
            ) or (
                call_name == "_schedule_resume_pending_sessions"
                and "restart_loop_guard_config" in has_keyword
            ):
                continue

            # _resolve_gateway_model only reads config when no snapshot is passed.
            is_snapshotless_model_read = (
                call_name == "_resolve_gateway_model"
                and not node.args
                and "config" not in has_keyword
            )
            if call_name not in prohibited and not is_snapshotless_model_read:
                continue

            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            if isinstance(parent, ast.AsyncFunctionDef) and parent.name not in startup_only:
                offenders.append(
                    (module_name, parent.name, call_name, node.lineno)
                )

    assert offenders == []


def test_async_gateway_functions_load_at_most_one_config_snapshot_per_call():
    """A turn should not repeat lock/deepcopy work for each display setting."""
    tree = ast.parse(inspect.getsource(gateway_run))
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    calls_by_function = {}
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_load_gateway_config_async"
        ):
            continue
        parent = parents.get(node)
        while parent is not None and not isinstance(
            parent, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            parent = parents.get(parent)
        if isinstance(parent, ast.AsyncFunctionDef):
            calls_by_function.setdefault(parent.name, []).append(node.lineno)

    repeated = {
        name: lines for name, lines in calls_by_function.items() if len(lines) > 1
    }
    assert repeated == {}
