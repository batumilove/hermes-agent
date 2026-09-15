"""Regression tests for best-effort active-agent status persistence."""

from __future__ import annotations

import asyncio


def test_active_agent_status_capacity_exhaustion_does_not_fail_turn_cleanup(
    monkeypatch,
):
    """Diagnostic status backpressure must not escape into message handling."""
    # Import after pytest's isolation fixtures establish HERMES_HOME.  Importing
    # gateway.run during collection freezes process-global path state and can
    # contaminate otherwise unrelated gateway tests.
    from gateway.config import GatewayConfig
    from gateway.run import GatewayRunner

    runner = GatewayRunner(GatewayConfig())

    async def _capacity_exhausted(*args, **kwargs):
        raise TimeoutError("serial daemon worker capacity exhausted")

    monkeypatch.setattr(
        "gateway.run.run_sync_in_detached_serial_daemon_thread",
        _capacity_exhausted,
    )

    asyncio.run(runner._persist_active_agents_async())
