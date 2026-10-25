"""A pause drains through the normal path without scheduling an independent restart."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest


def test_control_pause_propagates_updater_ownership(tmp_path, monkeypatch):
    from gateway import control_socket
    from gateway.run_bootstrap import _start_gateway_start_control_socket
    captured = {}

    class Server:
        def __init__(self, *, verb_handlers):
            captured.update(verb_handlers)

        async def start(self):
            return True

        def cleanup_files(self):
            pass

    monkeypatch.setattr(control_socket, "GatewayControlServer", Server)
    runner = Mock()
    runner.request_restart.return_value = True

    async def scenario():
        await _start_gateway_start_control_socket(runner)
        return await asyncio.to_thread(captured["pause-for-update"])

    ack = asyncio.run(scenario())
    assert ack["pausing"] is True
    runner.request_restart.assert_called_once_with(detached=False, via_service=True, for_update=True)


@pytest.mark.platforms("linux")
@pytest.mark.parametrize("for_update", [False, True])
def test_pause_suppresses_shortcut_but_ordinary_restart_keeps_it(monkeypatch, for_update):
    from gateway.run_shutdown import GatewayShutdownMixin
    from gateway.run_fork_contracts import GatewayForkContractsMixin
    from gateway import run
    import subprocess

    launches = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launches.append(a))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=str(run.os.getpid())))
    from hermes_platform import resolver
    monkeypatch.setattr(resolver, "locate_command", lambda name: SimpleNamespace(command=(name,)))
    monkeypatch.setenv("INVOCATION_ID", "fixture")
    runner = SimpleNamespace(_restart_task_started=False, _mark_api_runs_shutdown_requested=Mock(),
                             _await_active_work_before_restart=AsyncMock(), stop=AsyncMock())

    async def scenario():
        import inspect
        kwargs = {"for_update": for_update} if "for_update" in inspect.signature(GatewayShutdownMixin.request_restart).parameters else {}
        assert GatewayShutdownMixin.request_restart(runner, via_service=True, **kwargs)
        await runner._restart_task
        GatewayForkContractsMixin._launch_systemd_restart_shortcut(runner)

    asyncio.run(scenario())
    runner.stop.assert_awaited_once_with(restart=True, detached_restart=False, service_restart=True)
    assert len(launches) == (0 if for_update else 1)
