"""MCP suspect recovery and utility RPC teardown tracking contracts."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.tools.test_mcp_tool import _make_mock_server, _make_call_result


class TestDispatchRecovery:
    def _patch_mcp_loop(self):
        def fake_run(coro_or_factory, timeout=30):
            coro = coro_or_factory() if callable(coro_or_factory) else coro_or_factory
            return asyncio.run(coro)
        return patch("tools.mcp_tool_loop._run_on_mcp_loop", side_effect=fake_run)

    def test_suspect_connection_is_checked_before_dispatch(self):
        from tools.mcp_tool_handlers import _make_tool_handler
        from tools.mcp_tool import _servers

        mock_session = MagicMock()
        mock_session.call_tool = AsyncMock(
            return_value=_make_call_result("healthy", is_error=False)
        )
        server = _make_mock_server("test_srv", session=mock_session)
        server._suspect_reason = "prior timeout"
        _servers["test_srv"] = server

        try:
            handler = _make_tool_handler("test_srv", "greet", 120)
            with patch("tools.mcp_tool_handlers._ensure_healthy_or_recycle") as ensure, \
                 self._patch_mcp_loop():
                result = json.loads(handler({"name": "world"}))
            assert result["result"] == "healthy"
            ensure.assert_called_once_with(server, "test_srv")
        finally:
            _servers.pop("test_srv", None)

    def test_successful_call_does_not_leak_watchdog_coroutine(self):
        import gc
        import warnings
        from tools.mcp_tool_handlers import _make_tool_handler
        from tools.mcp_tool import _servers

        mock_session = MagicMock()
        mock_session.call_tool = AsyncMock(
            return_value=_make_call_result("healthy", is_error=False)
        )
        server = _make_mock_server("test_srv", session=mock_session)
        _servers["test_srv"] = server

        try:
            handler = _make_tool_handler("test_srv", "greet", 120)
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with self._patch_mcp_loop():
                    result = json.loads(handler({"name": "world"}))
                gc.collect()
            assert result["result"] == "healthy"
            assert not any(
                "_watch_stdio_children' was never awaited" in str(w.message)
                for w in caught
            )
        finally:
            _servers.pop("test_srv", None)


class TestUtilityDispatchRecovery:
    _patch_mcp_loop = TestDispatchRecovery._patch_mcp_loop

    @pytest.mark.parametrize(
        ("factory_name", "session_method", "arguments", "result"),
        [
            (
                "_make_list_resources_handler",
                "list_resources",
                {},
                SimpleNamespace(resources=[]),
            ),
            (
                "_make_read_resource_handler",
                "read_resource",
                {"uri": "file:///tmp/test.txt"},
                SimpleNamespace(contents=[]),
            ),
            (
                "_make_list_prompts_handler",
                "list_prompts",
                {},
                SimpleNamespace(prompts=[]),
            ),
            (
                "_make_get_prompt_handler",
                "get_prompt",
                {"name": "summarize", "arguments": {}},
                SimpleNamespace(messages=[], description=None),
            ),
        ],
    )
    def test_utility_rpc_is_tracked_for_teardown(
        self, factory_name, session_method, arguments, result,
    ):
        from tools import mcp_tool

        server = _make_mock_server("srv", session=MagicMock())

        async def tracked_rpc(*_args, **_kwargs):
            assert asyncio.current_task() in server._inflight_tasks
            return result

        setattr(server.session, session_method, tracked_rpc)
        mcp_tool._servers["srv"] = server

        try:
            from tools import mcp_tool_handlers as handlers
            handler = getattr(handlers, factory_name)("srv", 120)
            with self._patch_mcp_loop():
                response = json.loads(handler(arguments))
            assert "error" not in response
            assert server._inflight_tasks == set()
        finally:
            mcp_tool._servers.pop("srv", None)

    @pytest.mark.parametrize(
        ("factory_name", "session_method", "arguments", "result"),
        [
            (
                "_make_list_resources_handler",
                "list_resources",
                {},
                SimpleNamespace(resources=[]),
            ),
            (
                "_make_read_resource_handler",
                "read_resource",
                {"uri": "file:///tmp/test.txt"},
                SimpleNamespace(contents=[]),
            ),
            (
                "_make_list_prompts_handler",
                "list_prompts",
                {},
                SimpleNamespace(prompts=[]),
            ),
            (
                "_make_get_prompt_handler",
                "get_prompt",
                {"name": "summarize", "arguments": {}},
                SimpleNamespace(messages=[], description=None),
            ),
        ],
    )
    def test_utility_rpc_recycles_suspect_session_before_dispatch(
        self, factory_name, session_method, arguments, result,
    ):
        from tools import mcp_tool

        stale_session = MagicMock()
        setattr(
            stale_session,
            session_method,
            AsyncMock(side_effect=AssertionError("stale session was dispatched")),
        )
        fresh_session = MagicMock()
        fresh_rpc = AsyncMock(return_value=result)
        setattr(fresh_session, session_method, fresh_rpc)
        server = _make_mock_server("srv", session=stale_session)
        server._suspect_reason = "prior transport race"
        mcp_tool._servers["srv"] = server

        def recycle(active_server, server_name):
            assert active_server is server
            assert server_name == "srv"
            active_server.session = fresh_session

        try:
            from tools import mcp_tool_handlers as handlers
            handler = getattr(handlers, factory_name)("srv", 120)
            with patch.object(
                handlers, "_ensure_healthy_or_recycle", side_effect=recycle,
            ) as ensure_healthy, self._patch_mcp_loop():
                response = json.loads(handler(arguments))
            assert "error" not in response
            ensure_healthy.assert_called_once_with(server, "srv")
            fresh_rpc.assert_awaited_once()
            getattr(stale_session, session_method).assert_not_awaited()
        finally:
            mcp_tool._servers.pop("srv", None)
