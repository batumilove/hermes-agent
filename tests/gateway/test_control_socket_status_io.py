"""A control status query must not move filesystem reads onto the gateway loop."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from gateway.control_socket import query_gateway_control
from gateway.run_bootstrap import _start_gateway_start_control_socket


@pytest.mark.platforms("posix")
@pytest.mark.asyncio
async def test_live_status_query_keeps_loop_alive_while_status_read_is_blocked(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from gateway import control_socket

    entered = threading.Event()
    release = threading.Event()
    def blocked_status_read():
        entered.set()
        assert release.wait(timeout=2)
        return {"gateway_state": "running"}
    runner = SimpleNamespace(
        config=SimpleNamespace(multiplex_profiles=False),
        session_runtime_descriptor={},
        session_ticket_store=None,
        _running_agent_count=lambda: 1,
        _active_cron_job_count=lambda: 2,
        _active_api_run_count=lambda: 3,
        _active_delegation_count=lambda: 4,
        _cron_thread=SimpleNamespace(is_alive=lambda: True),
    )
    server = await _start_gateway_start_control_socket(runner)
    assert server is not None
    monkeypatch.setattr(control_socket, "build_status_payload", blocked_status_read)
    loop = asyncio.get_running_loop()
    started = loop.time()
    request = asyncio.create_task(asyncio.to_thread(query_gateway_control, tmp_path, "status", timeout=3))
    async def read_started():
        while not entered.is_set():
            await asyncio.sleep(0)
    try:
        await asyncio.wait_for(read_started(), timeout=0.5)
        assert loop.time() - started < 0.5
        assert not request.done()
    finally:
        release.set()
        response = await asyncio.wait_for(request, timeout=4)
        await server.stop()
    assert response["active_agents"] == 10
    assert response["occupancy"] == {
        "foreground_agents": 1, "cron_runs": 2, "api_runs": 3,
        "detached_workers": 4, "total": 10,
    }
