"""
Tests for Telegram polling network error recovery.

Specifically tests the fix for #3173 — when start_polling() fails after a
network error, the adapter must self-reschedule the next reconnect attempt
rather than silently leaving polling dead.
"""

import ast
import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig


def _ensure_telegram_mock():
    if "telegram" in sys.modules and hasattr(sys.modules["telegram"], "__file__"):
        return

    telegram_mod = MagicMock()
    telegram_mod.ext.ContextTypes.DEFAULT_TYPE = type(None)
    telegram_mod.constants.ParseMode.MARKDOWN_V2 = "MarkdownV2"
    telegram_mod.constants.ChatType.GROUP = "group"
    telegram_mod.constants.ChatType.SUPERGROUP = "supergroup"
    telegram_mod.constants.ChatType.CHANNEL = "channel"
    telegram_mod.constants.ChatType.PRIVATE = "private"

    for name in ("telegram", "telegram.ext", "telegram.constants", "telegram.request"):
        sys.modules.setdefault(name, telegram_mod)


_ensure_telegram_mock()

from plugins.platforms.telegram import adapter as tg_adapter  # noqa: E402
from plugins.platforms.telegram.adapter import TelegramAdapter  # noqa: E402
from gateway.run import GatewayRunner  # noqa: E402


@pytest.fixture(autouse=True)
def _no_auto_discovery(monkeypatch):
    """Disable DoH discovery and isolate process-local Telegram token claims."""
    async def _noop():
        return []

    monkeypatch.setattr("plugins.platforms.telegram.adapter.discover_fallback_ips", _noop)
    with tg_adapter._TELEGRAM_TOKEN_LOCK_OWNERS_GUARD:
        tg_adapter._TELEGRAM_TOKEN_LOCK_OWNERS.clear()
    yield
    with tg_adapter._TELEGRAM_TOKEN_LOCK_OWNERS_GUARD:
        tg_adapter._TELEGRAM_TOKEN_LOCK_OWNERS.clear()


def _make_adapter() -> TelegramAdapter:
    return TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))


async def _complete_current_polling_generation(adapter: TelegramAdapter) -> None:
    verifier = adapter._polling_progress_verifier_task
    adapter._record_polling_progress(adapter._polling_generation)
    if verifier is not None:
        await verifier


@pytest.mark.asyncio
async def test_reconnect_self_schedules_on_start_polling_failure():
    """
    When start_polling() raises during a network error retry, the adapter must
    schedule a new _handle_polling_network_error task — otherwise polling stays
    dead with no further error callbacks to trigger recovery.

    Regression test for #3173: gateway becomes unresponsive after Telegram 502.
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1

    mock_updater = MagicMock()
    mock_updater.running = True
    mock_updater.stop = AsyncMock()
    mock_updater.start_polling = AsyncMock(side_effect=Exception("Timed out"))

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(Exception("Bad Gateway"))

    # A retry task must have been added to _background_tasks
    pending = [t for t in adapter._background_tasks if not t.done()]
    assert len(pending) >= 1, (
        "Expected at least one self-rescheduled retry task in _background_tasks "
        f"after start_polling failure, got {len(pending)}"
    )

    # Clean up — cancel the pending retry so it doesn't run after the test
    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_reconnect_does_not_self_schedule_when_fatal_error_set():
    """
    When a fatal error is already set, the failed reconnect should NOT create
    another retry task — the gateway is already shutting down this adapter.
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1
    adapter._set_fatal_error("telegram_network_error", "already fatal", retryable=True)

    mock_updater = MagicMock()
    mock_updater.running = True
    mock_updater.stop = AsyncMock()
    mock_updater.start_polling = AsyncMock(side_effect=Exception("Timed out"))

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    adapter._app = mock_app

    initial_count = len(adapter._background_tasks)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(Exception("Timed out"))

    assert len(adapter._background_tasks) == initial_count, (
        "Should not schedule a retry when a fatal error is already set"
    )


@pytest.mark.asyncio
async def test_reconnect_chained_retry_updates_polling_error_task():
    """
    When start_polling() fails and the handler self-schedules a retry, that
    retry task must become the new `_polling_error_task` — otherwise the
    reentrancy guard used by the heartbeat loop, the pending-updates probe,
    and the PTB error callback goes stale while a recovery is still in
    flight, letting a second concurrent recovery start for the same outage.

    Regression test for the race behind the "half-destroyed adapter" bug
    (gateway reports connected but silently stops processing messages).
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1

    mock_updater = MagicMock()
    mock_updater.running = True
    mock_updater.stop = AsyncMock()
    mock_updater.start_polling = AsyncMock(side_effect=Exception("Timed out"))

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(Exception("Bad Gateway"))

    assert adapter._polling_error_task is not None
    assert not adapter._polling_error_task.done()

    adapter._polling_error_task.cancel()
    try:
        await adapter._polling_error_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_reconnect_success_waits_for_progress_to_reset_error_count():
    """
    start_polling() return alone cannot reset the network-error count.
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 3

    mock_updater = MagicMock()
    mock_updater.running = True
    mock_updater.stop = AsyncMock()
    mock_updater.start_polling = AsyncMock()  # succeeds

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot.get_me = AsyncMock(return_value=MagicMock())  # heartbeat probe path
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(Exception("Bad Gateway"))

    assert adapter._polling_network_error_count == 4
    assert adapter._send_path_degraded is True

    await _complete_current_polling_generation(adapter)
    assert adapter._polling_network_error_count == 0
    assert adapter._send_path_degraded is False

    # Clean up the heartbeat-probe task scheduled after a successful reconnect.
    pending = [t for t in adapter._background_tasks if not t.done()]
    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_reconnect_triggers_fatal_after_max_retries():
    """
    After MAX_NETWORK_RETRIES attempts, the adapter should set a fatal error
    rather than retrying forever.
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 10  # MAX_NETWORK_RETRIES

    fatal_handler = AsyncMock()
    adapter.set_fatal_error_handler(fatal_handler)

    mock_app = MagicMock()
    adapter._app = mock_app

    await adapter._handle_polling_network_error(Exception("still failing"))

    assert adapter.has_fatal_error
    assert adapter.fatal_error_code == "telegram_network_error"
    fatal_handler.assert_called_once()


@pytest.mark.asyncio
async def test_retry_exhaustion_queues_reconnect_before_child_disconnect(tmp_path):
    """Fatal teardown must not cancel the gateway's reconnect handoff.

    The gateway runs ``disconnect()`` in a bounded child task.  If the current
    polling-recovery owner remains in ``_polling_error_task``, Telegram teardown
    cancels that parent while it is still awaiting the fatal handler, so the
    handler never gets to queue background reconnection.
    """
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = _make_adapter()
    adapter._polling_network_error_count = 10  # MAX_NETWORK_RETRIES
    adapter.set_fatal_error_handler(runner._handle_adapter_fatal_error)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters

    recovery_task = asyncio.create_task(
        adapter._handle_polling_network_error(Exception("still failing"))
    )
    adapter._polling_error_task = recovery_task
    result = await asyncio.gather(recovery_task, return_exceptions=True)

    assert result == [None]
    assert runner.adapters == {}
    assert Platform.TELEGRAM in runner._failed_platforms
    assert runner._failed_platforms[Platform.TELEGRAM]["attempts"] == 0


@pytest.mark.asyncio
async def test_heartbeat_watchdog_handoff_survives_child_disconnect(tmp_path):
    """The wedged-recovery heartbeat watchdog must survive its fatal callback.

    The heartbeat loop force-escalates a stuck polling-recovery task.  Like
    the network/conflict terminal paths, the heartbeat task itself is the
    owner that ``disconnect()`` cancels, so the fatal callback must release
    ``_polling_heartbeat_task`` before notifying the runner.
    """
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = _make_adapter()
    adapter.set_fatal_error_handler(runner._handle_adapter_fatal_error)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters

    # Simulate the heartbeat watchdog's fatal-escalation path directly.
    adapter._set_fatal_error(
        "telegram_network_error",
        "Telegram reconnect task wedged; forcing gateway reconnect.",
        retryable=True,
    )
    heartbeat_task = asyncio.create_task(adapter._handoff_polling_fatal_error())
    adapter._polling_heartbeat_task = heartbeat_task
    result = await asyncio.gather(heartbeat_task, return_exceptions=True)

    assert result == [None]
    assert runner.adapters == {}
    assert Platform.TELEGRAM in runner._failed_platforms


# ---------------------------------------------------------------------------
# Connection pool drain tests (PR #16466 salvage)
# ---------------------------------------------------------------------------

def _make_mock_app():
    """Build a mock Application with an explicit polling request object."""
    mock_polling_req = AsyncMock()
    mock_polling_req.shutdown = AsyncMock()
    mock_polling_req.initialize = AsyncMock()

    mock_bot = MagicMock()
    mock_bot._request = (mock_polling_req, MagicMock())  # (getUpdates, general)

    mock_updater = MagicMock()
    mock_updater.running = True
    mock_updater.stop = AsyncMock()
    mock_updater.start_polling = AsyncMock()

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot = mock_bot
    return mock_app, mock_polling_req


@pytest.mark.asyncio
async def test_reconnect_drains_polling_request_only():
    """During reconnect, only the polling request (_request[0]) must be cycled.

    The general request (_request[1]) must NOT be touched — doing so would
    break concurrent send_message / edit_message calls.
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1

    mock_app, mock_polling_req = _make_mock_app()
    adapter._app = mock_app

    general_req = mock_app.bot._request[1]

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(Exception("Bad Gateway"))

    # Polling request must be shut down and re-initialized
    mock_polling_req.shutdown.assert_called_once()
    mock_polling_req.initialize.assert_called_once()

    # General request must NOT be touched
    general_req.shutdown.assert_not_called()
    general_req.initialize.assert_not_called()

    # Reconnect must still succeed
    mock_app.updater.start_polling.assert_called_once()
    assert adapter._polling_network_error_count == 2
    await _complete_current_polling_generation(adapter)
    assert adapter._polling_network_error_count == 0


@pytest.mark.asyncio
async def test_reconnect_continues_if_drain_fails():
    """If the polling request drain raises, start_polling must still proceed."""
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1

    mock_app, mock_polling_req = _make_mock_app()
    # Both shutdown and initialize fail
    mock_polling_req.shutdown = AsyncMock(side_effect=Exception("shutdown boom"))
    mock_polling_req.initialize = AsyncMock(side_effect=Exception("init boom"))
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(Exception("Bad Gateway"))

    # start_polling must still be called despite drain failure
    mock_app.updater.start_polling.assert_called_once()
    assert adapter._polling_network_error_count == 2
    await _complete_current_polling_generation(adapter)
    assert adapter._polling_network_error_count == 0


@pytest.mark.asyncio
async def test_initialize_still_runs_when_shutdown_fails():
    """If shutdown() raises, initialize() must still be attempted.

    This prevents a failed shutdown from leaving the request pool in a
    permanently closed state.
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1

    mock_app, mock_polling_req = _make_mock_app()
    mock_polling_req.shutdown = AsyncMock(side_effect=Exception("shutdown boom"))
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(Exception("Bad Gateway"))

    # initialize MUST be called even though shutdown raised
    mock_polling_req.initialize.assert_called_once()
    mock_app.updater.start_polling.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_continues_if_drain_hangs(monkeypatch):
    """If the polling request drain HANGS (wedged httpx pool close on a
    CLOSE-WAIT socket), the reconnect ladder must still advance rather than
    freezing the tracked _polling_error_task forever.

    Regression test for #66377: an unbounded ``shutdown()`` /
    ``initialize()`` in ``_drain_polling_connections`` leaves the handler
    task pending, which gates every escalation path and silently kills the
    gateway. The drain awaits are bounded by ``_DRAIN_TIMEOUT``, so the
    handler must complete and reach ``start_polling`` within a hard bound.
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1

    mock_app, mock_polling_req = _make_mock_app()

    async def _hang(*args, **kwargs):
        await asyncio.Event().wait()  # never returns

    # Both drain awaits wedge indefinitely.
    mock_polling_req.shutdown = AsyncMock(side_effect=_hang)
    mock_polling_req.initialize = AsyncMock(side_effect=_hang)
    adapter._app = mock_app

    # Keep the drain timeout tiny so the test stays fast; the real default
    # is generous enough not to truncate healthy closes.
    monkeypatch.setattr(tg_adapter, "_DRAIN_TIMEOUT", 0.01, raising=False)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        # Hard outer bound: on unfixed code the drain hangs forever and this
        # trips; with the fix the inner wait_for releases well before it.
        await asyncio.wait_for(
            adapter._handle_polling_network_error(Exception("Timed out")),
            timeout=5,
        )

    # Ladder advanced past the wedged drain despite it never returning.
    mock_app.updater.start_polling.assert_called_once()
    assert adapter._polling_network_error_count == 2
    # The tracked task must not be stuck pending — otherwise every
    # escalation path stays gated behind an in-flight guard.
    assert (
        adapter._polling_error_task is None
        or adapter._polling_error_task.done()
    )


@pytest.mark.asyncio
async def test_heartbeat_force_escalates_wedged_recovery_task(monkeypatch):
    """#66377: the heartbeat is an independent, cause-agnostic watchdog.

    Every recovery path (ladder re-entry, pending-update probe, PTB error
    callback) gates new recovery on ``_polling_error_task.done()``. If that task
    wedges on ANY hung await — not just the drain closed by #66492 — the gateway
    stays alive but deaf with nothing retrying. The heartbeat must detect a
    recovery task that stays in-flight past ``_POLLING_ERROR_TASK_STUCK_TIMEOUT``
    and force a retryable-fatal so the background reconnector rebuilds the
    adapter.
    """
    adapter = _make_adapter()

    async def _wedged():
        await asyncio.Event().wait()  # never completes — simulates the hang

    wedged_task = asyncio.ensure_future(_wedged())
    adapter._polling_error_task = wedged_task

    mock_bot = MagicMock()
    mock_bot.get_me = AsyncMock()
    mock_app = MagicMock()
    mock_app.bot = mock_bot
    adapter._app = mock_app
    adapter._probe_pending_updates = AsyncMock()
    adapter._notify_fatal_error = AsyncMock()

    # Controllable monotonic clock advanced by each (mocked) heartbeat sleep so
    # the same wedged task is observed across the stuck threshold deterministically.
    clock = [1000.0]

    async def _fake_sleep(*_a, **_k):
        clock[0] += 200.0

    monkeypatch.setattr(tg_adapter.time, "monotonic", lambda: clock[0])

    with patch("asyncio.sleep", new=AsyncMock(side_effect=_fake_sleep)):
        await asyncio.wait_for(adapter._polling_heartbeat_loop(), timeout=5)

    assert adapter.has_fatal_error, "wedged recovery task must force a fatal escalation"
    adapter._notify_fatal_error.assert_awaited()

    wedged_task.cancel()
    try:
        await wedged_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_conflict_retry_also_drains_polling_connections():
    """_handle_polling_conflict must also drain the polling pool on retry."""
    adapter = _make_adapter()
    adapter._polling_conflict_count = 0

    mock_app, mock_polling_req = _make_mock_app()
    adapter._app = mock_app

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_conflict(Exception("Conflict: terminated by other getUpdates"))

    # Polling request must be drained during conflict retry too
    mock_polling_req.shutdown.assert_called_once()
    mock_polling_req.initialize.assert_called_once()
    mock_app.updater.start_polling.assert_called_once()


@pytest.mark.asyncio
async def test_drain_helper_noop_without_app():
    """_drain_polling_connections must be a no-op when _app is None."""
    adapter = _make_adapter()
    adapter._app = None
    # Should not raise
    await adapter._drain_polling_connections()


# ── Heartbeat probe ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_polling_verifier_exits_on_matching_progress(monkeypatch):
    """
    Matching getUpdates progress exits without probing the general path.
    """
    adapter = _make_adapter()

    mock_updater = MagicMock()
    mock_updater.running = True

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot.get_me = AsyncMock(return_value=MagicMock())
    adapter._app = mock_app

    adapter._handle_polling_network_error = AsyncMock()
    generation, progress = adapter._begin_polling_generation()
    adapter._record_polling_progress(generation)
    monkeypatch.setattr(tg_adapter, "_POLLING_PROGRESS_TIMEOUT", 0)

    await adapter._verify_polling_after_reconnect(generation, progress)

    mock_app.bot.get_me.assert_not_awaited()
    adapter._handle_polling_network_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_probe_reenters_ladder_when_updater_not_running(monkeypatch):
    """
    If Updater.running is False at the progress deadline, re-enter recovery.
    """
    adapter = _make_adapter()

    mock_updater = MagicMock()
    mock_updater.running = False

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot.get_me = AsyncMock()
    adapter._app = mock_app

    adapter._handle_polling_network_error = AsyncMock()
    generation, progress = adapter._begin_polling_generation()
    monkeypatch.setattr(tg_adapter, "_POLLING_PROGRESS_TIMEOUT", 0)

    await adapter._verify_polling_after_reconnect(generation, progress)

    mock_app.bot.get_me.assert_not_called()
    # Recovery is scheduled through _schedule_polling_recovery (#63243), so
    # the ladder runs as the tracked _polling_error_task.
    task = adapter._polling_error_task
    assert task is not None
    await task
    adapter._handle_polling_network_error.assert_awaited_once()
    err = adapter._handle_polling_network_error.await_args.args[0]
    assert isinstance(err, RuntimeError)
    assert "not running" in str(err).lower()


@pytest.mark.asyncio
async def test_heartbeat_probe_reenters_ladder_when_get_me_times_out(monkeypatch):
    """
    If bot.get_me() hangs longer than PROBE_TIMEOUT, treat as wedged.
    Simulates the connection-pool wedge that motivated this fix.
    """
    adapter = _make_adapter()

    mock_updater = MagicMock()
    mock_updater.running = True

    async def hang_forever(*args, **kwargs):
        await asyncio.sleep(3600)

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot.get_me = AsyncMock(side_effect=hang_forever)
    adapter._app = mock_app

    adapter._handle_polling_network_error = AsyncMock()
    generation, progress = adapter._begin_polling_generation()
    monkeypatch.setattr(tg_adapter, "_POLLING_PROGRESS_TIMEOUT", 0)

    async def fast_wait_for(coro, timeout):
        if asyncio.iscoroutine(coro):
            coro.close()
        raise asyncio.TimeoutError()

    with patch("plugins.platforms.telegram.adapter.asyncio.wait_for", new=fast_wait_for):
        await adapter._verify_polling_after_reconnect(generation, progress)

    task = adapter._polling_error_task
    assert task is not None
    await task
    adapter._handle_polling_network_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_heartbeat_probe_reenters_ladder_on_get_me_network_error(monkeypatch):
    """
    Any exception raised by bot.get_me() (NetworkError, ConnectionError, etc.)
    should re-enter the reconnect ladder with the original exception.
    """
    adapter = _make_adapter()

    mock_updater = MagicMock()
    mock_updater.running = True

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot.get_me = AsyncMock(side_effect=ConnectionError("pool wedged"))
    adapter._app = mock_app

    adapter._handle_polling_network_error = AsyncMock()
    generation, progress = adapter._begin_polling_generation()
    monkeypatch.setattr(tg_adapter, "_POLLING_PROGRESS_TIMEOUT", 0)

    await adapter._verify_polling_after_reconnect(generation, progress)

    task = adapter._polling_error_task
    assert task is not None
    # _schedule_polling_recovery must also register the ladder in
    # _background_tasks so a failed recovery isn't silently GC'd.
    assert task in adapter._background_tasks
    await task
    adapter._handle_polling_network_error.assert_awaited_once()
    assert isinstance(
        adapter._handle_polling_network_error.await_args.args[0], ConnectionError
    )


@pytest.mark.asyncio
async def test_heartbeat_probe_ignores_auth_errors(monkeypatch):
    """
    Auth/validation failures from the post-reconnect probe must not enter the
    network-reconnect ladder (#63243): a revoked token would otherwise churn
    through stop/drain/start_polling cycles that mask the real failure.
    """
    adapter = _make_adapter()

    mock_updater = MagicMock()
    mock_updater.running = True

    # Name-shaped like PTB's InvalidToken; _looks_like_network_error excludes
    # it by class name, matching real PTB semantics.
    invalid_token = type("InvalidToken", (Exception,), {})("token revoked")

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot.get_me = AsyncMock(side_effect=invalid_token)
    adapter._app = mock_app

    adapter._handle_polling_network_error = AsyncMock()
    generation, progress = adapter._begin_polling_generation()
    monkeypatch.setattr(tg_adapter, "_POLLING_PROGRESS_TIMEOUT", 0)

    await adapter._verify_polling_after_reconnect(generation, progress)

    assert adapter._polling_error_task is None
    adapter._handle_polling_network_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_probe_defers_to_inflight_recovery(monkeypatch):
    """
    A probe failure while another recovery is mid-flight must not start a
    second concurrent stop/drain/start_polling sequence (#63243) — overlapping
    recoveries produce dueling getUpdates sessions (self-inflicted 409s).
    """
    adapter = _make_adapter()

    mock_updater = MagicMock()
    mock_updater.running = True

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot.get_me = AsyncMock(side_effect=ConnectionError("pool wedged"))
    adapter._app = mock_app

    inflight = MagicMock()
    inflight.done.return_value = False
    adapter._polling_error_task = inflight

    adapter._handle_polling_network_error = AsyncMock()
    generation, progress = adapter._begin_polling_generation()
    monkeypatch.setattr(tg_adapter, "_POLLING_PROGRESS_TIMEOUT", 0)

    await adapter._verify_polling_after_reconnect(generation, progress)

    assert adapter._polling_error_task is inflight
    adapter._handle_polling_network_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_probe_skips_when_already_fatal(monkeypatch):
    """
    If the adapter is already in fatal-error state by the time the probe
    delay elapses, the probe should bail without further action.
    """
    adapter = _make_adapter()
    adapter._set_fatal_error("telegram_polling_conflict", "already fatal", retryable=False)

    mock_app = MagicMock()
    mock_app.bot.get_me = AsyncMock()
    adapter._app = mock_app

    adapter._handle_polling_network_error = AsyncMock()
    generation, progress = adapter._begin_polling_generation()
    monkeypatch.setattr(tg_adapter, "_POLLING_PROGRESS_TIMEOUT", 0)

    await adapter._verify_polling_after_reconnect(generation, progress)

    mock_app.bot.get_me.assert_not_called()
    adapter._handle_polling_network_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconnect_schedules_heartbeat_probe_on_success():
    """
    After a successful start_polling() in the reconnect path, a probe task
    must be added to _background_tasks. Without it, a wedged Updater would
    sit silent indefinitely with no further error_callback to advance the
    reconnect ladder.
    """
    adapter = _make_adapter()
    adapter._polling_network_error_count = 1

    mock_updater = MagicMock()
    mock_updater.running = True
    mock_updater.stop = AsyncMock()
    mock_updater.start_polling = AsyncMock()  # succeeds

    mock_app = MagicMock()
    mock_app.updater = mock_updater
    mock_app.bot.get_me = AsyncMock(return_value=MagicMock())
    adapter._app = mock_app

    initial_count = len(adapter._background_tasks)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        await adapter._handle_polling_network_error(Exception("Bad Gateway"))

    assert len(adapter._background_tasks) > initial_count, (
        "Expected a heartbeat probe task to be scheduled after a successful "
        "reconnect's start_polling()"
    )

    # Clean up.
    pending = [t for t in adapter._background_tasks if not t.done()]
    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass


# ── Persistent heartbeat loop (_polling_heartbeat_loop) ──────────────────────
#
# These tests cover the continuous CLOSE-WAIT detection loop that fixes the bug
# (#48495) where a dead Telegram TCP socket caused the gateway to stop receiving
# messages silently. The _verify_polling_after_reconnect tests above cover the
# one-shot post-reconnect probe; these cover the background loop that runs for
# the gateway's full lifetime in polling mode.
#
# Loop structure: while True: sleep(INTERVAL) → fatal/app checks → get_me().
# So with cancel raised on the Nth patched sleep, get_me() fires (N-1) times.


@pytest.mark.asyncio
async def test_heartbeat_loop_exits_cleanly_on_cancel():
    """The heartbeat loop must exit without raising when cancelled (normal shutdown)."""
    adapter = _make_adapter()

    mock_app = MagicMock()
    mock_app.bot.get_me = AsyncMock(return_value=MagicMock())
    adapter._app = mock_app

    sleep_count = 0

    async def fast_sleep(seconds):
        nonlocal sleep_count
        sleep_count += 1
        # sleep #1 → get_me, sleep #2 → get_me, sleep #3 → cancel.
        if sleep_count >= 3:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fast_sleep):
        # Should not raise — CancelledError is swallowed internally.
        await adapter._polling_heartbeat_loop()

    assert mock_app.bot.get_me.await_count == 2


@pytest.mark.asyncio
async def test_heartbeat_loop_triggers_reconnect_on_timeout():
    """A TimeoutError from get_me() must schedule a reconnect via _handle_polling_network_error."""
    adapter = _make_adapter()
    adapter._handle_polling_network_error = AsyncMock()

    mock_app = MagicMock()
    adapter._app = mock_app

    sleep_call = 0

    async def fast_sleep(seconds):
        nonlocal sleep_call
        sleep_call += 1
        if sleep_call >= 3:
            raise asyncio.CancelledError()

    async def fast_wait_for(coro, timeout):
        if asyncio.iscoroutine(coro):
            coro.close()
        raise asyncio.TimeoutError()

    with patch("asyncio.sleep", side_effect=fast_sleep):
        with patch("plugins.platforms.telegram.adapter.asyncio.wait_for", side_effect=fast_wait_for):
            await adapter._polling_heartbeat_loop()

    # A reconnect task must have been created.
    assert adapter._polling_error_task is not None


@pytest.mark.asyncio
async def test_heartbeat_loop_triggers_reconnect_on_os_error():
    """An OSError (e.g. connection reset) from get_me() must trigger a reconnect."""
    adapter = _make_adapter()
    adapter._handle_polling_network_error = AsyncMock()

    mock_app = MagicMock()
    adapter._app = mock_app

    sleep_call = 0

    async def fast_sleep(seconds):
        nonlocal sleep_call
        sleep_call += 1
        if sleep_call >= 3:
            raise asyncio.CancelledError()

    async def os_error_wait_for(coro, timeout):
        if asyncio.iscoroutine(coro):
            coro.close()
        raise OSError("Connection reset by peer")

    with patch("asyncio.sleep", side_effect=fast_sleep):
        with patch("plugins.platforms.telegram.adapter.asyncio.wait_for", side_effect=os_error_wait_for):
            await adapter._polling_heartbeat_loop()

    assert adapter._polling_error_task is not None


@pytest.mark.asyncio
async def test_heartbeat_loop_skips_reconnect_if_already_in_progress():
    """If a reconnect task is already running, the heartbeat must not spawn another."""
    adapter = _make_adapter()

    # Simulate an already-running reconnect task.
    existing_task = asyncio.get_event_loop().create_task(asyncio.sleep(3600))
    adapter._polling_error_task = existing_task
    adapter._handle_polling_network_error = AsyncMock()

    mock_app = MagicMock()
    adapter._app = mock_app

    sleep_call = 0

    async def fast_sleep(seconds):
        nonlocal sleep_call
        sleep_call += 1
        if sleep_call >= 3:
            raise asyncio.CancelledError()

    async def timeout_wait_for(coro, timeout):
        if asyncio.iscoroutine(coro):
            coro.close()
        raise asyncio.TimeoutError()

    with patch("asyncio.sleep", side_effect=fast_sleep):
        with patch("plugins.platforms.telegram.adapter.asyncio.wait_for", side_effect=timeout_wait_for):
            await adapter._polling_heartbeat_loop()

    # _handle_polling_network_error must NOT have been called — existing task still running.
    adapter._handle_polling_network_error.assert_not_awaited()

    existing_task.cancel()
    try:
        await existing_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.mark.asyncio
async def test_heartbeat_loop_ignores_non_connectivity_errors():
    """Errors that are not connectivity failures (e.g. TelegramError) must be swallowed."""
    adapter = _make_adapter()
    adapter._handle_polling_network_error = AsyncMock()

    mock_app = MagicMock()
    adapter._app = mock_app

    sleep_call = 0

    async def fast_sleep(seconds):
        nonlocal sleep_call
        sleep_call += 1
        if sleep_call >= 3:
            raise asyncio.CancelledError()

    async def telegram_error_wait_for(coro, timeout):
        if asyncio.iscoroutine(coro):
            coro.close()
        raise RuntimeError("TelegramError: Unauthorized")  # non-OSError, non-TimeoutError

    with patch("asyncio.sleep", side_effect=fast_sleep):
        with patch("plugins.platforms.telegram.adapter.asyncio.wait_for", side_effect=telegram_error_wait_for):
            await adapter._polling_heartbeat_loop()

    # No reconnect should have been triggered for a non-connectivity error.
    adapter._handle_polling_network_error.assert_not_awaited()


async def _heartbeat_exception_case(exc, *, pending_probe=False):
    adapter = _make_adapter()
    reconnect_handler = AsyncMock()
    adapter._handle_polling_network_error = reconnect_handler  # type: ignore[method-assign]
    mock_app = MagicMock()
    mock_app.updater.running = True
    if pending_probe:
        mock_app.bot.get_me = AsyncMock(return_value=MagicMock())
        mock_app.bot.get_webhook_info = AsyncMock(side_effect=exc)
    else:
        mock_app.bot.get_me = AsyncMock(side_effect=exc)
    adapter._app = mock_app

    sleep_calls = 0

    async def fast_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 2:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fast_sleep):
        await adapter._polling_heartbeat_loop()
    await asyncio.sleep(0)
    return adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_probe", [False, True])
async def test_heartbeat_routes_ptb_transport_errors_to_reconnect(pending_probe):
    from telegram.error import NetworkError, TimedOut

    for exc in (NetworkError("network"), TimedOut("timeout")):
        adapter = await _heartbeat_exception_case(exc, pending_probe=pending_probe)
        reconnect_handler = adapter._handle_polling_network_error
        assert isinstance(reconnect_handler, AsyncMock)
        reconnect_handler.assert_awaited_once_with(exc)
        assert adapter._polling_error_task is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_probe", [False, True])
async def test_heartbeat_ignores_ptb_semantic_errors(pending_probe):
    from telegram.error import BadRequest, Forbidden, InvalidToken, RetryAfter

    for exc in (
        BadRequest("bad request"),
        Forbidden("forbidden"),
        InvalidToken("invalid token"),
        RetryAfter(1),
    ):
        adapter = await _heartbeat_exception_case(exc, pending_probe=pending_probe)
        reconnect_handler = adapter._handle_polling_network_error
        assert isinstance(reconnect_handler, AsyncMock)
        reconnect_handler.assert_not_awaited()
        assert adapter._polling_error_task is None


@pytest.mark.parametrize(
    ("error_name", "expected"),
    [
        ("NetworkError", True),
        ("TimedOut", True),
        ("BadRequest", False),
        ("Forbidden", False),
        ("InvalidToken", False),
        ("RetryAfter", False),
    ],
)
def test_network_error_classifier_matches_ptb_semantics(error_name, expected):
    import telegram.error as telegram_error

    error_type = getattr(telegram_error, error_name)
    error = error_type(1) if error_name == "RetryAfter" else error_type(error_name)
    assert TelegramAdapter._looks_like_network_error(error) is expected


def _calls_shared_network_classifier(node):
    return any(
        isinstance(child, ast.Call)
        and isinstance(child.func, ast.Attribute)
        and child.func.attr == "_looks_like_network_error"
        for child in ast.walk(node)
    )


def test_polling_error_callback_uses_shared_network_classifier():
    source = Path(TelegramAdapter.connect.__code__.co_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    callbacks = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_polling_error_callback"
    ]
    assert len(callbacks) == 1
    assert _calls_shared_network_classifier(callbacks[0])


def test_connect_initialize_retry_uses_shared_network_classifier():
    source = Path(TelegramAdapter.connect.__code__.co_filename).read_text(encoding="utf-8")
    tree = ast.parse(source)
    connect_functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"connect", "_connect_generation"}
    ]
    exception_handlers = [
        node
        for function in connect_functions
        for node in ast.walk(function)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "Exception"
    ]
    assert any(_calls_shared_network_classifier(handler) for handler in exception_handlers)


@pytest.mark.asyncio
async def test_heartbeat_loop_exits_on_fatal_error():
    """A fatal error short-circuits the loop before probing get_me()."""
    adapter = _make_adapter()
    adapter._set_fatal_error("telegram_network_error", "boom", retryable=True)

    mock_app = MagicMock()
    mock_app.bot.get_me = AsyncMock(return_value=MagicMock())
    adapter._app = mock_app

    async def fast_sleep(seconds):
        return None

    with patch("asyncio.sleep", side_effect=fast_sleep):
        await adapter._polling_heartbeat_loop()

    # Fatal error returns before the get_me() probe.
    mock_app.bot.get_me.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnect_cancels_heartbeat_task():
    """disconnect() must cancel the heartbeat task before shutting down the app."""
    adapter = _make_adapter()

    # Simulate a running heartbeat.
    heartbeat_task = asyncio.get_event_loop().create_task(asyncio.sleep(3600))
    adapter._polling_heartbeat_task = heartbeat_task

    mock_app = MagicMock()
    mock_app.updater = MagicMock()
    mock_app.updater.running = False
    mock_app.running = False
    mock_app.shutdown = AsyncMock()
    adapter._app = mock_app

    await adapter.disconnect()

    assert heartbeat_task.cancelled(), "Heartbeat task must be cancelled by disconnect()"
    assert adapter._polling_heartbeat_task is None


# ── Bootstrap degradation: keep polling alive during outages (#47508) ────


@pytest.mark.asyncio
async def test_delete_webhook_network_error_is_recoverable():
    """deleteWebhook timeouts must not fail gateway startup.

    A transient Bot API outage during bootstrap should be treated as
    recoverable and continue toward polling, so it never becomes a systemd
    service failure.
    """
    adapter = _make_adapter()
    mock_bot = MagicMock()
    mock_bot.delete_webhook = AsyncMock(side_effect=ConnectionError("api.telegram.org timeout"))
    adapter._bot = mock_bot

    result = await adapter._delete_webhook_best_effort()

    assert result is False
    assert adapter._send_path_degraded is True
    mock_bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
    assert not adapter.has_fatal_error


@pytest.mark.asyncio
async def test_polling_bootstrap_network_error_schedules_background_recovery():
    """Initial start_polling() network failure should degrade, not raise."""
    adapter = _make_adapter()
    mock_updater = MagicMock()
    mock_updater.start_polling = AsyncMock(side_effect=ConnectionError("bootstrap timeout"))
    mock_app = MagicMock()
    mock_app.updater = mock_updater
    adapter._app = mock_app
    adapter._schedule_polling_recovery = MagicMock()

    result = await adapter._start_polling_resilient(
        drop_pending_updates=True,
        error_callback=lambda error: None,
    )

    assert result is False
    adapter._schedule_polling_recovery.assert_called_once()
    err = adapter._schedule_polling_recovery.call_args.args[0]
    assert isinstance(err, ConnectionError)
    assert adapter._schedule_polling_recovery.call_args.kwargs["reason"] == "polling bootstrap"
    assert not adapter.has_fatal_error


@pytest.mark.asyncio
async def test_polling_bootstrap_conflict_schedules_conflict_recovery_task():
    """Initial 409 polling conflict should also be recovered in background."""
    adapter = _make_adapter()
    mock_updater = MagicMock()
    mock_updater.start_polling = AsyncMock(
        side_effect=Exception("Conflict: terminated by other getUpdates request")
    )
    mock_app = MagicMock()
    mock_app.updater = mock_updater
    adapter._app = mock_app
    adapter._handle_polling_conflict = AsyncMock()

    result = await adapter._start_polling_resilient(
        drop_pending_updates=True,
        error_callback=lambda error: None,
    )

    assert result is False
    pending = [t for t in adapter._background_tasks if not t.done()]
    assert pending, "expected background conflict recovery task"
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    assert not adapter.has_fatal_error


@pytest.mark.asyncio
async def test_schedule_polling_recovery_tracks_background_task():
    """Background recovery task is registered so it isn't GC'd mid-flight."""
    adapter = _make_adapter()
    adapter._handle_polling_network_error = AsyncMock()

    adapter._schedule_polling_recovery(ConnectionError("boom"), reason="unit test")

    assert adapter._send_path_degraded is True
    assert adapter._polling_error_task is not None
    assert adapter._polling_error_task in adapter._background_tasks
    await adapter._polling_error_task
    adapter._handle_polling_network_error.assert_awaited_once()


async def _assert_unterminated_owner_fences_recovery(handler_name: str) -> None:
    """A timed-out PTB owner may not be overlapped by another generation."""
    adapter = _make_adapter()
    app = MagicMock()
    app.updater = MagicMock()
    app.updater.running = True

    never_finishes = asyncio.Event()

    async def _hanging_stop() -> None:
        await never_finishes.wait()

    app.updater.stop = AsyncMock(side_effect=_hanging_stop)
    app.updater.start_polling = AsyncMock()
    adapter._app = app
    adapter._drain_polling_connections = AsyncMock()
    adapter._handoff_polling_fatal_error = AsyncMock()
    generation_before = adapter._polling_generation

    import plugins.platforms.telegram.adapter as _mod

    with (
        patch.object(_mod, "_UPDATER_STOP_TIMEOUT", 0.01),
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        await asyncio.wait_for(
            getattr(adapter, handler_name)(OSError("CLOSE-WAIT test")),
            timeout=0.5,
        )

    app.updater.stop.assert_awaited_once()
    adapter._drain_polling_connections.assert_not_awaited()
    app.updater.start_polling.assert_not_awaited()
    assert adapter._polling_generation == generation_before
    assert adapter.has_fatal_error
    assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"
    assert adapter._polling_teardown_started is True
    adapter._handoff_polling_fatal_error.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name",
    ["_handle_polling_network_error", "_handle_polling_conflict"],
)
async def test_updater_stop_timeout_fences_all_polling_recovery(handler_name):
    """Network and conflict recovery must fail closed on unproven teardown."""
    await _assert_unterminated_owner_fences_recovery(handler_name)


@pytest.mark.asyncio
async def test_unterminated_polling_owner_requests_process_recycle(tmp_path):
    """The runner must not replace an adapter whose old owner may still run."""
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = _make_adapter()
    adapter._set_fatal_error(
        "telegram_polling_owner_unterminated",
        "Telegram polling owner did not terminate before its deadline.",
        retryable=True,
    )
    adapter._polling_teardown_started = True
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters
    runner.request_restart = MagicMock(return_value=True)
    adapter.disconnect = AsyncMock()

    await runner._handle_adapter_fatal_error_impl(adapter)

    getattr(runner, "request_restart").assert_called_once_with(
        detached=False, via_service=True
    )
    adapter.disconnect.assert_not_awaited()
    assert runner.adapters == {Platform.TELEGRAM: adapter}
    assert Platform.TELEGRAM not in runner._failed_platforms


@pytest.mark.asyncio

async def test_updater_stop_hard_deadline_tracks_cancellation_suppressing_task(monkeypatch):
    """A stop coroutine that suppresses cancellation cannot hold recovery open."""
    adapter = _make_adapter()
    app, polling_request = _make_mock_app()
    adapter._app = app
    adapter._drain_polling_connections = AsyncMock()
    adapter._start_polling_once = AsyncMock()
    release_stop = asyncio.Event()
    stop_suppressed_cancellation = asyncio.Event()
    stop_tasks = []

    async def _cancellation_suppressing_stop():
        stop_tasks.append(asyncio.current_task())
        try:
            await release_stop.wait()
        except asyncio.CancelledError:
            stop_suppressed_cancellation.set()
            await release_stop.wait()

    app.updater.stop = AsyncMock(side_effect=_cancellation_suppressing_stop)
    generation_before = adapter._polling_generation
    monkeypatch.setattr(tg_adapter, "_UPDATER_STOP_TIMEOUT", 0.01)
    real_sleep = asyncio.sleep

    async def _yielding_sleep(*_args, **_kwargs):
        await real_sleep(0)

    recovery = None
    observed_bounded_completion = False
    cleanup_completed = False
    results = []
    try:
        with patch("asyncio.sleep", new=AsyncMock(side_effect=_yielding_sleep)):
            recovery = asyncio.create_task(
                adapter._handle_polling_network_error(OSError("owner stuck"))
            )
            done, _pending = await asyncio.wait({recovery}, timeout=2.0)
            observed_bounded_completion = recovery in done

        await asyncio.wait_for(stop_suppressed_cancellation.wait(), timeout=2.0)
        app.updater.stop.assert_awaited_once()
        adapter._drain_polling_connections.assert_not_awaited()
        polling_request.shutdown.assert_not_awaited()
        adapter._start_polling_once.assert_not_awaited()
        assert adapter._polling_generation == generation_before
        assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"
        assert adapter._polling_teardown_started is True
        assert not stop_tasks[0].done()
    finally:
        release_stop.set()
        cleanup_tasks = {
            task for task in ([recovery] + stop_tasks) if task is not None
        }
        if cleanup_tasks:
            done, pending = await asyncio.wait(cleanup_tasks, timeout=2.0)
            for task in pending:
                task.cancel()
            if pending:
                cancelled_done, pending = await asyncio.wait(pending, timeout=2.0)
                done |= cancelled_done
            cleanup_completed = not pending
            if done:
                results = await asyncio.gather(*done, return_exceptions=True)

    assert observed_bounded_completion is True
    assert cleanup_completed is True
    assert not any(isinstance(result, BaseException) for result in results)
    await asyncio.sleep(0)
    assert stop_tasks[0].done()


@pytest.mark.asyncio
async def test_updater_stop_cancelled_error_distinguishes_internal_from_external(monkeypatch):
    """Internal CancelledError fences ownership; caller cancellation still propagates."""
    monkeypatch.setattr(tg_adapter, "_UPDATER_STOP_TIMEOUT", 0.05)

    externally_cancelled = _make_adapter()
    external_app, _ = _make_mock_app()
    stop_entered = asyncio.Event()
    release_stop = asyncio.Event()
    stop_suppressed_cancellation = asyncio.Event()
    stop_tasks = []

    async def _blocked_stop():
        stop_tasks.append(asyncio.current_task())
        stop_entered.set()
        try:
            await release_stop.wait()
        except asyncio.CancelledError:
            stop_suppressed_cancellation.set()
            await release_stop.wait()

    external_app.updater.stop = AsyncMock(side_effect=_blocked_stop)
    externally_cancelled._app = external_app
    real_sleep = asyncio.sleep

    async def _yielding_sleep(*_args, **_kwargs):
        await real_sleep(0)

    recovery = None
    suppression_waiter = None
    suppression_observed = False
    external_cancel_propagated = False
    cleanup_completed = False
    results = []
    with patch("asyncio.sleep", new=AsyncMock(side_effect=_yielding_sleep)):
        try:
            recovery = asyncio.create_task(
                externally_cancelled._handle_polling_network_error(OSError("cancel me"))
            )
            await asyncio.wait_for(stop_entered.wait(), timeout=2.0)
            recovery.cancel()
            suppression_waiter = asyncio.create_task(
                stop_suppressed_cancellation.wait()
            )
            done, _pending = await asyncio.wait({suppression_waiter}, timeout=2.0)
            suppression_observed = suppression_waiter in done
        finally:
            release_stop.set()
            cleanup_tasks = {
                task
                for task in ([recovery, suppression_waiter] + stop_tasks)
                if task is not None
            }
            if cleanup_tasks:
                done, pending = await asyncio.wait(cleanup_tasks, timeout=2.0)
                for task in pending:
                    task.cancel()
                if pending:
                    cancelled_done, pending = await asyncio.wait(pending, timeout=2.0)
                    done |= cancelled_done
                cleanup_completed = not pending
                if done:
                    results = await asyncio.gather(*done, return_exceptions=True)
            external_cancel_propagated = (
                recovery is not None and recovery.done() and recovery.cancelled()
            )

    assert suppression_observed is True
    assert external_cancel_propagated is True
    assert cleanup_completed is True
    assert any(isinstance(result, asyncio.CancelledError) for result in results)
    assert externally_cancelled.fatal_error_code is None

    internally_cancelled = _make_adapter()
    internal_app, _ = _make_mock_app()
    internal_app.updater.stop = AsyncMock(side_effect=asyncio.CancelledError())
    internally_cancelled._app = internal_app
    internally_cancelled._drain_polling_connections = AsyncMock()
    internally_cancelled._start_polling_once = AsyncMock()
    internally_cancelled._handoff_polling_fatal_error = AsyncMock()

    with patch("asyncio.sleep", new=AsyncMock(side_effect=_yielding_sleep)):
        await internally_cancelled._handle_polling_network_error(
            OSError("stop cancelled itself")
        )

    assert internally_cancelled.fatal_error_code == "telegram_polling_owner_unterminated"
    internally_cancelled._drain_polling_connections.assert_not_awaited()
    internally_cancelled._start_polling_once.assert_not_awaited()
    internally_cancelled._handoff_polling_fatal_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_stop_exception_retains_unterminated_owner(tmp_path):
    """A generic stop failure is not a clean Telegram disconnect."""
    adapter = _make_adapter()
    app, _ = _make_mock_app()
    app.updater.stop = AsyncMock(side_effect=RuntimeError("PTB stop exploded"))
    app.running = True
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    adapter._app = app
    adapter._bot = app.bot
    adapter._updater_start_attempted = True

    await adapter.disconnect()

    app.updater.stop.assert_awaited_once()
    assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"

    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters
    runner.request_restart = MagicMock(return_value=True)
    await runner._handle_adapter_fatal_error_impl(adapter)

    assert runner.adapters == {Platform.TELEGRAM: adapter}
    assert Platform.TELEGRAM not in runner._failed_platforms
    runner.request_restart.assert_called_once_with(detached=False, via_service=True)


@pytest.mark.asyncio
async def test_runner_cleanup_timeout_preserves_retryable_adapter_owner(tmp_path):
    """An outer cleanup deadline cannot authorize removal or retry queuing."""
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = _make_adapter()
    adapter._set_fatal_error("telegram_network_error", "ordinary retry", retryable=True)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters
    runner.request_restart = MagicMock(return_value=True)
    runner._adapter_disconnect_timeout_secs = lambda: 0.01
    disconnect_started = asyncio.Event()
    release_disconnect = asyncio.Event()
    disconnect_tasks = []

    async def _blocked_disconnect():
        disconnect_tasks.append(asyncio.current_task())
        disconnect_started.set()
        try:
            await release_disconnect.wait()
        except asyncio.CancelledError:
            await release_disconnect.wait()

    adapter.disconnect = AsyncMock(side_effect=_blocked_disconnect)
    handler = None
    observed_bounded_completion = False
    cleanup_completed = False
    results = []
    try:
        handler = asyncio.create_task(
            runner._handle_adapter_fatal_error_impl(adapter)
        )
        done, _pending = await asyncio.wait({handler}, timeout=2.0)
        observed_bounded_completion = handler in done
        await asyncio.wait_for(disconnect_started.wait(), timeout=2.0)

        assert runner.adapters == {Platform.TELEGRAM: adapter}
        assert Platform.TELEGRAM not in runner._failed_platforms
        runner.request_restart.assert_called_once_with(detached=False, via_service=True)
        assert disconnect_tasks and not disconnect_tasks[0].done()
    finally:
        release_disconnect.set()
        cleanup_tasks = {
            task for task in ([handler] + disconnect_tasks) if task is not None
        }
        if cleanup_tasks:
            done, pending = await asyncio.wait(cleanup_tasks, timeout=2.0)
            for task in pending:
                task.cancel()
            if pending:
                cancelled_done, pending = await asyncio.wait(pending, timeout=2.0)
                done |= cancelled_done
            cleanup_completed = not pending
            if done:
                results = await asyncio.gather(*done, return_exceptions=True)

    assert observed_bounded_completion is True
    assert cleanup_completed is True
    assert not any(isinstance(result, BaseException) for result in results)


@pytest.mark.asyncio
async def test_polling_fatal_handoff_is_adapter_level_one_shot():
    """Changing fatal codes cannot make concurrent paths notify the runner twice."""
    adapter = _make_adapter()
    handler_entered = asyncio.Event()
    release_handler = asyncio.Event()
    calls = 0

    async def _fatal_handler(_adapter):
        nonlocal calls
        calls += 1
        handler_entered.set()
        await release_handler.wait()

    adapter.set_fatal_error_handler(_fatal_handler)
    adapter._set_fatal_error("telegram_network_error", "network", retryable=True)
    first = None
    second = None
    results = []
    try:
        first = asyncio.create_task(adapter._handoff_polling_fatal_error())
        await asyncio.wait_for(handler_entered.wait(), timeout=2.0)
        adapter._set_fatal_error(
            "telegram_polling_conflict", "conflict", retryable=False
        )
        second = asyncio.create_task(adapter._handoff_polling_fatal_error())
        await asyncio.sleep(0)
    finally:
        release_handler.set()
        pending = [task for task in (first, second) if task is not None]
        if pending:
            results = await asyncio.gather(*pending, return_exceptions=True)

    assert not any(isinstance(result, BaseException) for result in results)
    assert calls == 1


@pytest.mark.asyncio
async def test_polling_owner_stop_is_serialized_and_rechecked(monkeypatch):
    """Concurrent recoveries may never overlap updater.stop calls."""
    adapter = _make_adapter()
    app, _ = _make_mock_app()
    adapter._app = app
    adapter._drain_polling_connections = AsyncMock()
    adapter._start_polling_once = AsyncMock()
    first_stop_entered = asyncio.Event()
    release_first_stop = asyncio.Event()
    stop_call_count = 0
    concurrent_stops = 0
    max_concurrent_stops = 0

    async def _serialized_stop():
        nonlocal stop_call_count, concurrent_stops, max_concurrent_stops
        stop_call_count += 1
        concurrent_stops += 1
        max_concurrent_stops = max(max_concurrent_stops, concurrent_stops)
        first_stop_entered.set()
        await release_first_stop.wait()
        concurrent_stops -= 1
        app.updater.running = False

    app.updater.stop = AsyncMock(side_effect=_serialized_stop)
    monkeypatch.setattr(tg_adapter, "_UPDATER_STOP_TIMEOUT", 0.2)

    network = None
    conflict = None
    observed_before_release = None
    results = []
    real_sleep = asyncio.sleep

    async def _yielding_sleep(*_args, **_kwargs):
        await real_sleep(0)

    try:
        with patch("asyncio.sleep", new=AsyncMock(side_effect=_yielding_sleep)):
            network = asyncio.create_task(
                adapter._handle_polling_network_error(OSError("network"))
            )
            await asyncio.wait_for(first_stop_entered.wait(), timeout=2.0)
            conflict = asyncio.create_task(
                adapter._handle_polling_conflict(RuntimeError("conflict"))
            )
            await real_sleep(0)
            await real_sleep(0)
            observed_before_release = app.updater.stop.await_count
            assert not conflict.done()
    finally:
        release_first_stop.set()
        pending = [task for task in (network, conflict) if task is not None]
        if pending:
            with patch("asyncio.sleep", new=AsyncMock(side_effect=_yielding_sleep)):
                results = await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=2.0
                )

    assert not any(isinstance(result, BaseException) for result in results)
    assert observed_before_release == 1
    assert stop_call_count == 1
    assert max_concurrent_stops == 1


@pytest.mark.asyncio
async def test_polling_owner_stop_serialized_caller_rechecks_still_running(monkeypatch):
    """A queued recovery retries stop, without overlap, if ownership remains live."""
    adapter = _make_adapter()
    app, _ = _make_mock_app()
    adapter._app = app
    adapter._drain_polling_connections = AsyncMock()
    adapter._start_polling_once = AsyncMock()
    first_stop_entered = asyncio.Event()
    release_first_stop = asyncio.Event()
    stop_call_count = 0
    concurrent_stops = 0
    max_concurrent_stops = 0

    async def _serialized_stop():
        nonlocal stop_call_count, concurrent_stops, max_concurrent_stops
        stop_call_count += 1
        concurrent_stops += 1
        max_concurrent_stops = max(max_concurrent_stops, concurrent_stops)
        if stop_call_count == 1:
            first_stop_entered.set()
            await release_first_stop.wait()
            # First clean return did not change PTB's externally observed owner state.
            app.updater.running = True
        else:
            app.updater.running = False
        concurrent_stops -= 1

    app.updater.stop = AsyncMock(side_effect=_serialized_stop)
    monkeypatch.setattr(tg_adapter, "_UPDATER_STOP_TIMEOUT", 0.2)

    network = None
    conflict = None
    results = []
    real_sleep = asyncio.sleep

    async def _yielding_sleep(*_args, **_kwargs):
        await real_sleep(0)

    try:
        with patch("asyncio.sleep", new=AsyncMock(side_effect=_yielding_sleep)):
            network = asyncio.create_task(
                adapter._handle_polling_network_error(OSError("network"))
            )
            await asyncio.wait_for(first_stop_entered.wait(), timeout=2.0)
            conflict = asyncio.create_task(
                adapter._handle_polling_conflict(RuntimeError("conflict"))
            )
            await real_sleep(0)
            await real_sleep(0)
            assert app.updater.stop.await_count == 1
            assert not conflict.done()
    finally:
        release_first_stop.set()
        pending = [task for task in (network, conflict) if task is not None]
        if pending:
            with patch("asyncio.sleep", new=AsyncMock(side_effect=_yielding_sleep)):
                results = await asyncio.wait_for(
                    asyncio.gather(*pending, return_exceptions=True), timeout=2.0
                )

    assert not any(isinstance(result, BaseException) for result in results)
    assert stop_call_count == 2
    assert max_concurrent_stops == 1

@pytest.mark.asyncio
async def test_cancellation_resistant_updater_stop_cannot_defeat_owner_deadline():
    """A stop coroutine that suppresses cancellation must not strand recovery."""
    adapter = _make_adapter()
    release_stop = asyncio.Event()
    stop_started = asyncio.Event()
    stop_cancelled = asyncio.Event()
    stop_finished = asyncio.Event()
    cancellation_count = 0

    async def _cancellation_resistant_stop():
        nonlocal cancellation_count
        stop_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_count += 1
            stop_cancelled.set()
            while not release_stop.is_set():
                try:
                    await release_stop.wait()
                except asyncio.CancelledError:
                    cancellation_count += 1
        finally:
            stop_finished.set()

    updater = MagicMock()
    updater.running = True
    updater.stop = AsyncMock(side_effect=_cancellation_resistant_stop)
    updater.start_polling = AsyncMock()
    app = MagicMock()
    app.updater = updater
    adapter._app = app
    adapter._drain_polling_connections = AsyncMock()
    adapter._handoff_polling_fatal_error = AsyncMock()

    with patch.object(tg_adapter, "_UPDATER_STOP_TIMEOUT", 0.01):
        handler_task = asyncio.create_task(
            adapter._handle_polling_network_error(Exception("CLOSE-WAIT test"))
        )
        try:
            # The production path has a real five-second first-retry backoff.
            # Do not patch the singleton asyncio module: the hard deadline and
            # detached cleanup must execute against the real event loop.
            await asyncio.wait_for(stop_started.wait(), timeout=6.0)
            await asyncio.wait_for(stop_cancelled.wait(), timeout=0.5)
            for _ in range(100):
                if handler_task.done():
                    break
                await asyncio.sleep(0.001)
            completed_by_deadline = handler_task.done()
            remained_pending = not stop_finished.is_set()
            cancellations_before_release = cancellation_count
        finally:
            release_stop.set()
            await asyncio.wait_for(stop_finished.wait(), timeout=0.5)
            await asyncio.wait_for(handler_task, timeout=0.5)

    assert completed_by_deadline, "cancellation-resistant stop defeated the deadline"
    assert remained_pending, "owner task was discarded before explicit release"
    assert cancellations_before_release == 1
    updater.stop.assert_awaited_once()
    assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"
    adapter._drain_polling_connections.assert_not_awaited()
    updater.start_polling.assert_not_awaited()
    adapter._handoff_polling_fatal_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_retains_owner_references_after_stop_timeout():
    """Shutdown may not claim/discard ownership that it could not terminate."""
    adapter = _make_adapter()
    never_finishes = asyncio.Event()

    updater = MagicMock()
    updater.running = True
    updater.stop = AsyncMock(side_effect=never_finishes.wait)
    app = MagicMock()
    app.updater = updater
    app.running = True
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    bot = MagicMock()
    adapter._app = app
    adapter._bot = bot
    adapter._updater_start_attempted = True
    adapter._set_status_indicator = AsyncMock()
    adapter._cancel_pending_delivery_tasks = AsyncMock()
    adapter._release_platform_lock = MagicMock()

    with patch.object(tg_adapter, "_UPDATER_STOP_TIMEOUT", 0.01):
        await asyncio.wait_for(adapter.disconnect(), timeout=0.5)

    assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"
    updater.stop.assert_awaited_once()
    assert adapter._app is app
    assert adapter._bot is bot
    app.stop.assert_awaited_once()
    app.shutdown.assert_awaited_once()



@pytest.mark.asyncio
@pytest.mark.parametrize("failure_site", ["app_stop", "app_shutdown"])
async def test_disconnect_retains_owner_after_terminal_app_failure(failure_site):
    """App/transport teardown failure cannot be reported as ownership proof."""
    adapter = _make_adapter()
    updater = MagicMock()
    updater.running = True
    updater.stop = AsyncMock()
    app = MagicMock()
    app.updater = updater
    app.running = True
    app.stop = AsyncMock()
    app.shutdown = AsyncMock()
    if failure_site == "app_stop":
        app.stop.side_effect = RuntimeError("app stop failed")
    else:
        app.shutdown.side_effect = RuntimeError("app shutdown failed")
    bot = MagicMock()
    adapter._app = app
    adapter._bot = bot
    adapter._set_status_indicator = AsyncMock()
    adapter._cancel_pending_delivery_tasks = AsyncMock()
    adapter._release_platform_lock = MagicMock()

    await adapter.disconnect()

    assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"
    assert adapter._polling_teardown_started is True
    assert adapter._app is app
    assert adapter._bot is bot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name",
    ["_handle_polling_network_error", "_handle_polling_conflict"],
)
async def test_owner_stop_baseexception_fences_recovery(handler_name):
    """Detached stop-task BaseException outcomes must fail closed."""
    class OwnerStopBaseFailure(BaseException):
        pass

    adapter = _make_adapter()
    app = MagicMock()
    app.updater = MagicMock()
    app.updater.running = True
    app.updater.stop = AsyncMock(side_effect=OwnerStopBaseFailure("owner failed"))
    adapter._app = app
    adapter._drain_polling_connections = AsyncMock()
    adapter._start_polling_once = AsyncMock()
    adapter._handoff_polling_fatal_error = AsyncMock()
    real_sleep = asyncio.sleep

    async def yielding_sleep(_delay):
        await real_sleep(0)

    with patch("asyncio.sleep", side_effect=yielding_sleep):
        await getattr(adapter, handler_name)(OSError("polling failed"))

    assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"
    adapter._drain_polling_connections.assert_not_awaited()
    adapter._start_polling_once.assert_not_awaited()
    adapter._handoff_polling_fatal_error.assert_awaited_once()


def test_abandoned_stop_task_baseexception_is_observed():
    class DetachedTaskBaseFailure(BaseException):
        pass

    task = MagicMock()
    task.exception.side_effect = DetachedTaskBaseFailure("detached failure")
    tg_adapter._consume_abandoned_task(task)
    task.exception.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler_name",
    ["_handle_polling_network_error", "_handle_polling_conflict"],
)
async def test_updater_stop_exception_fences_recovery(handler_name):
    """A stop exception is also unproven ownership, never permission to restart."""
    adapter = _make_adapter()
    app = MagicMock()
    app.updater = MagicMock()
    app.updater.running = True
    app.updater.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
    adapter._app = app
    adapter._drain_polling_connections = AsyncMock()
    adapter._start_polling_once = AsyncMock()
    adapter._handoff_polling_fatal_error = AsyncMock()
    real_sleep = asyncio.sleep

    async def yielding_sleep(_delay):
        await real_sleep(0)

    with patch("asyncio.sleep", side_effect=yielding_sleep):
        await getattr(adapter, handler_name)(OSError("polling failed"))

    app.updater.stop.assert_awaited_once()
    adapter._drain_polling_connections.assert_not_awaited()
    adapter._start_polling_once.assert_not_awaited()
    assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"
    assert adapter._polling_teardown_started is True
    adapter._handoff_polling_fatal_error.assert_awaited_once()


@pytest.mark.asyncio
async def test_network_and_conflict_recovery_share_one_generation_owner():
    """Mixed recovery callbacks may execute only one stop/drain/start sequence."""
    adapter = _make_adapter()
    app = MagicMock()
    updater = MagicMock()
    updater.running = True
    app.updater = updater
    adapter._app = app
    adapter._drain_polling_connections = AsyncMock()
    adapter._handoff_polling_fatal_error = AsyncMock()
    stop_entered = asyncio.Event()
    release_stop = asyncio.Event()
    active_stops = 0
    max_active_stops = 0

    async def _stop():
        nonlocal active_stops, max_active_stops
        active_stops += 1
        max_active_stops = max(max_active_stops, active_stops)
        stop_entered.set()
        try:
            await release_stop.wait()
        finally:
            active_stops -= 1

    async def _start(*_args, **_kwargs):
        adapter._polling_generation += 1

    updater.stop = AsyncMock(side_effect=_stop)
    adapter._start_polling_once = AsyncMock(side_effect=_start)
    real_sleep = asyncio.sleep

    async def _yielding_sleep(*_args, **_kwargs):
        await real_sleep(0)

    with patch("asyncio.sleep", new=AsyncMock(side_effect=_yielding_sleep)):
        network_task = asyncio.create_task(
            adapter._handle_polling_network_error(OSError("network"))
        )
        conflict_task = asyncio.create_task(
            adapter._handle_polling_conflict(RuntimeError("conflict"))
        )
        try:
            await asyncio.wait_for(stop_entered.wait(), timeout=1.5)
            for _ in range(20):
                await real_sleep(0)
            overlap_before_release = max_active_stops
        finally:
            release_stop.set()
            await asyncio.wait_for(
                asyncio.gather(network_task, conflict_task), timeout=2.0
            )

    assert overlap_before_release == 1
    updater.stop.assert_awaited_once()
    adapter._drain_polling_connections.assert_awaited_once()
    adapter._start_polling_once.assert_awaited_once()
    assert adapter._polling_generation == 1
    adapter._handoff_polling_fatal_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_stop_honors_failed_latch_when_updater_running_is_false():
    """PTB's running flag cannot erase a prior unproven stop attempt."""
    adapter = _make_adapter()
    app = MagicMock()
    app.updater = MagicMock(running=False)
    adapter._polling_owner_stop_failed = True
    adapter._handoff_polling_fatal_error = AsyncMock()

    terminated = await adapter._stop_polling_owner_for_recovery(
        app, context="running-flag regression"
    )

    assert terminated is False
    assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"
    assert adapter._polling_teardown_started is True
    app.updater.stop.assert_not_called()
    adapter._handoff_polling_fatal_error.assert_awaited_once()


def test_same_process_token_claim_blocks_reentrant_scoped_lock_acquire():
    """A PID-reentrant lock file cannot authorize two adapters for one token."""
    first = _make_adapter()
    second = _make_adapter()
    first._acquire_platform_lock = MagicMock(return_value=True)
    second._acquire_platform_lock = MagicMock(return_value=True)
    first._release_platform_lock = MagicMock()

    assert first._acquire_telegram_token_lock() is True
    assert second._acquire_telegram_token_lock() is False

    first._acquire_platform_lock.assert_called_once()
    second._acquire_platform_lock.assert_not_called()
    assert second.fatal_error_code == "telegram-bot-token_lock"

    first._release_telegram_token_lock()
    first._release_platform_lock.assert_called_once_with()


@pytest.mark.asyncio
async def test_disposal_marker_serializes_before_polling_start(tmp_path):
    """A disposal that wins the transition lock blocks start_polling atomically."""
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = _make_adapter()
    adapter._gateway_replacement_guard = runner._telegram_replacement_blocked
    adapter._gateway_owner_transition_lock = runner._telegram_owner_transition_lock
    disconnect_entered = asyncio.Event()
    release_disconnect = asyncio.Event()

    async def _blocked_disconnect():
        disconnect_entered.set()
        await release_disconnect.wait()

    adapter.disconnect = AsyncMock(side_effect=_blocked_disconnect)
    app = MagicMock()
    app.updater.start_polling = AsyncMock()
    cleanup = asyncio.create_task(
        runner._safe_adapter_disconnect(adapter, Platform.TELEGRAM)
    )
    try:
        await asyncio.wait_for(disconnect_entered.wait(), timeout=1.0)
        with pytest.raises(tg_adapter._PollingLifecycleAbort):
            await adapter._start_polling_once(
                app, drop_pending_updates=False, error_callback=None
            )
        app.updater.start_polling.assert_not_awaited()
    finally:
        release_disconnect.set()
        assert await asyncio.wait_for(cleanup, timeout=1.0) is True


@pytest.mark.asyncio
async def test_cancellation_resistant_polling_start_cannot_outlive_disposal(
    tmp_path, monkeypatch
):
    """Pending updater start is retained and makes disposal fail closed."""
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = _make_adapter()
    adapter._gateway_replacement_guard = runner._telegram_replacement_blocked
    adapter._gateway_owner_transition_lock = runner._telegram_owner_transition_lock
    start_entered = asyncio.Event()
    release_start = asyncio.Event()

    async def _resistant_start(**_kwargs):
        start_entered.set()
        while not release_start.is_set():
            try:
                await release_start.wait()
            except asyncio.CancelledError:
                continue

    app = MagicMock(running=False)
    app.updater = MagicMock(running=False)
    app.updater.start_polling = AsyncMock(side_effect=_resistant_start)
    app.updater.stop = AsyncMock()
    app.shutdown = AsyncMock()
    adapter._app = app
    adapter._bot = app.bot
    adapter._release_telegram_token_lock = MagicMock()
    adapter._set_status_indicator = AsyncMock()
    adapter._cancel_pending_delivery_tasks = AsyncMock()
    monkeypatch.setattr(tg_adapter, "_UPDATER_STOP_TIMEOUT", 0.01)

    start = asyncio.create_task(
        adapter._start_polling_once(
            app, drop_pending_updates=False, error_callback=None
        )
    )
    try:
        await asyncio.wait_for(start_entered.wait(), timeout=1.0)
        completed = await asyncio.wait_for(
            runner._safe_adapter_disconnect(adapter, Platform.TELEGRAM),
            timeout=1.0,
        )
        assert completed is False
        assert runner._telegram_owner_replacement_fenced is True
        assert adapter.fatal_error_code == "telegram_polling_owner_unterminated"
        assert adapter._app is app
        adapter._release_telegram_token_lock.assert_not_called()
        app.updater.stop.assert_not_awaited()
        assert await adapter.connect(is_reconnect=True) is False
        assert adapter._polling_teardown_started is True
    finally:
        release_start.set()
        result = await asyncio.wait_for(
            asyncio.gather(start, return_exceptions=True), timeout=1.0
        )
        assert isinstance(result[0], asyncio.CancelledError)


@pytest.mark.asyncio
async def test_cancellation_resistant_webhook_start_cannot_publish_after_disposal(
    tmp_path, monkeypatch
):
    """Late webhook completion remains fenced and cannot become connected."""
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = _make_adapter()
    adapter._gateway_replacement_guard = runner._telegram_replacement_blocked
    adapter._gateway_owner_transition_lock = runner._telegram_owner_transition_lock
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _resistant_webhook(**_kwargs):
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    app = MagicMock(running=False)
    app.updater = MagicMock(running=False)
    app.updater.start_webhook = AsyncMock(side_effect=_resistant_webhook)
    app.updater.stop = AsyncMock()
    app.shutdown = AsyncMock()
    adapter._app = app
    adapter._bot = app.bot
    adapter._release_telegram_token_lock = MagicMock()
    adapter._set_status_indicator = AsyncMock()
    adapter._cancel_pending_delivery_tasks = AsyncMock()
    monkeypatch.setattr(tg_adapter, "_UPDATER_STOP_TIMEOUT", 0.01)

    start = asyncio.create_task(adapter._start_webhook_once(app, listen="127.0.0.1"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1.0)
        completed = await asyncio.wait_for(
            runner._safe_adapter_disconnect(adapter, Platform.TELEGRAM),
            timeout=1.0,
        )
        assert completed is False
        assert runner._telegram_owner_replacement_fenced is True
        adapter._release_telegram_token_lock.assert_not_called()
    finally:
        release.set()
        result = await asyncio.wait_for(
            asyncio.gather(start, return_exceptions=True), timeout=1.0
        )
        assert isinstance(result[0], asyncio.CancelledError)
    assert adapter.is_connected() is False


@pytest.mark.asyncio
async def test_same_adapter_connect_generations_are_serialized():
    """Only one connect generation may perform admission/application install."""
    adapter = _make_adapter()
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    active = 0
    max_active = 0

    async def _generation(*, is_reconnect=False):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if not is_reconnect:
            first_entered.set()
            await release_first.wait()
        active -= 1
        return True

    adapter._connect_application_generation = AsyncMock(side_effect=_generation)
    first = asyncio.create_task(adapter.connect())
    await asyncio.wait_for(first_entered.wait(), timeout=1.0)
    second = asyncio.create_task(adapter.connect(is_reconnect=True))
    await asyncio.sleep(0)
    assert max_active == 1
    release_first.set()
    assert await asyncio.wait_for(first, timeout=1.0) is True
    assert await asyncio.wait_for(second, timeout=1.0) is True
    assert max_active == 1


@pytest.mark.asyncio
async def test_cancelled_transition_wait_fences_before_propagating(tmp_path):
    """Cancellation before marker activation must still retain the central fence."""
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = _make_adapter()
    adapter.disconnect = AsyncMock()
    await runner._telegram_owner_transition_lock.acquire()
    cleanup = asyncio.create_task(
        runner._safe_adapter_disconnect(adapter, Platform.TELEGRAM)
    )
    try:
        await asyncio.sleep(0)
        cleanup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cleanup
        assert id(adapter) in runner._telegram_owner_disposals
        assert runner._telegram_owner_replacement_fenced is True
        adapter.disconnect.assert_not_called()
    finally:
        runner._telegram_owner_transition_lock.release()


@pytest.mark.asyncio
async def test_never_attempted_polling_disconnect_skips_updater_stop():
    """Pre-polling startup failure has positive proof that no updater owner exists."""
    adapter = _make_adapter()
    app = MagicMock(running=False)
    app.updater = MagicMock(running=False)
    app.updater.stop = AsyncMock(
        side_effect=RuntimeError("This Updater is not running!")
    )
    app.shutdown = AsyncMock()
    adapter._app = app
    adapter._bot = app.bot
    adapter._updater_start_attempted = False
    adapter._release_telegram_token_lock = MagicMock()
    adapter._set_status_indicator = AsyncMock()
    adapter._cancel_pending_delivery_tasks = AsyncMock()

    await adapter.disconnect()

    app.updater.stop.assert_not_awaited()
    app.shutdown.assert_awaited_once()
    adapter._release_telegram_token_lock.assert_called_once_with()
    assert adapter._app is None
    assert adapter.fatal_error_code is None


@pytest.mark.asyncio
async def test_guard_refusal_cannot_publish_connected_adapter(monkeypatch):
    """A lifecycle-aborted polling start must make connect() fail closed."""
    app = MagicMock()
    app.bot = MagicMock()
    app.initialize = AsyncMock()
    app.start = AsyncMock()
    app.add_handler = MagicMock()

    builder = MagicMock()
    builder.token.return_value = builder
    builder.request.return_value = builder
    builder.get_updates_request.return_value = builder
    builder.build.return_value = app
    application = MagicMock()
    application.builder.return_value = builder
    monkeypatch.setattr(tg_adapter, "Application", application)
    monkeypatch.setattr(tg_adapter, "HTTPXRequest", MagicMock)
    monkeypatch.setattr(tg_adapter, "resolve_proxy_url", lambda *a, **k: None)

    adapter = _make_adapter()
    adapter._acquire_platform_lock = MagicMock(return_value=True)
    adapter._release_platform_lock = MagicMock()
    adapter._fallback_ips = MagicMock(return_value=[])
    adapter._delete_webhook_best_effort = AsyncMock()

    async def _refuse_polling(*_args, **_kwargs):
        adapter._polling_teardown_started = True
        adapter._send_path_degraded = True
        return False

    adapter._start_polling_resilient = AsyncMock(side_effect=_refuse_polling)
    adapter._mark_connected = MagicMock()
    adapter._polling_heartbeat_loop = AsyncMock()
    adapter._start_post_connect_housekeeping = MagicMock()

    assert await adapter.connect() is False

    adapter._mark_connected.assert_not_called()
    adapter._start_post_connect_housekeeping.assert_not_called()
    assert adapter.is_connected is False
    adapter._release_telegram_token_lock()


def _owner_fatal_runner(tmp_path):
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    runner.request_restart = MagicMock(return_value=True)
    # New tests assert the centralized fence objects; they are not persisted
    # into real GatewayRunner state, so seed the defaults explicitly.
    runner._telegram_owner_disposals = set()
    runner._retained_telegram_owner_adapters = set()
    runner._telegram_owner_replacement_fenced = False
    runner._telegram_owner_recycle_requested = False
    return runner


@pytest.mark.asyncio
async def test_stale_owner_fatal_callback_cannot_request_recycle(tmp_path):
    """Only the currently installed Telegram owner may recycle the process."""
    runner = _owner_fatal_runner(tmp_path)
    adapter = _make_adapter()
    adapter._set_fatal_error(
        "telegram_polling_owner_unterminated", "stale", retryable=True
    )
    adapter._polling_teardown_started = True
    runner.adapters = {}
    runner.delivery_router.adapters = runner.adapters
    runner._update_platform_runtime_status = MagicMock()

    await runner._handle_adapter_fatal_error_impl(adapter)

    runner.request_restart.assert_not_called()
    runner._update_platform_runtime_status.assert_not_called()
    assert runner.adapters == {}
    assert Platform.TELEGRAM not in runner._failed_platforms


@pytest.mark.asyncio
async def test_owner_fatal_code_on_nontelegram_adapter_cannot_recycle(tmp_path):
    """The recycle trust boundary requires the current Telegram adapter type."""
    config = GatewayConfig(
        platforms={
            Platform.DISCORD: PlatformConfig(enabled=True, token="test-token")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    adapter = MagicMock()
    adapter.platform = Platform.DISCORD
    adapter.fatal_error_code = "telegram_polling_owner_unterminated"
    adapter.fatal_error_message = "spoofed"
    adapter.fatal_error_retryable = True
    adapter._polling_teardown_started = True
    runner.adapters = {Platform.DISCORD: adapter}
    runner.delivery_router.adapters = runner.adapters
    runner.request_restart = MagicMock(return_value=True)
    runner._safe_adapter_disconnect = AsyncMock(return_value=True)

    await runner._handle_adapter_fatal_error_impl(adapter)

    runner.request_restart.assert_not_called()
    runner._safe_adapter_disconnect.assert_awaited_once_with(
        adapter, Platform.DISCORD
    )
    assert Platform.DISCORD in runner._failed_platforms


@pytest.mark.asyncio
async def test_owner_fatal_code_without_teardown_fence_cannot_recycle(tmp_path):
    """The dedicated code alone is not authority without adapter fence state."""
    runner = _owner_fatal_runner(tmp_path)
    adapter = _make_adapter()
    adapter._set_fatal_error(
        "telegram_polling_owner_unterminated", "untrusted code only", retryable=True
    )
    assert adapter._polling_teardown_started is False
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters
    runner._safe_adapter_disconnect = AsyncMock(return_value=True)

    await runner._handle_adapter_fatal_error_impl(adapter)

    runner.request_restart.assert_not_called()
    runner._safe_adapter_disconnect.assert_awaited_once_with(
        adapter, Platform.TELEGRAM
    )
    assert Platform.TELEGRAM in runner._failed_platforms


@pytest.mark.asyncio
async def test_telegram_disconnect_timeout_recycles_instead_of_replacing(tmp_path):
    """An outer cleanup timeout is unproven ownership and must fail closed."""
    runner = _owner_fatal_runner(tmp_path)
    adapter = _make_adapter()
    adapter._set_fatal_error("telegram_network_error", "network", retryable=True)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters
    runner._safe_adapter_disconnect = AsyncMock(return_value=False)

    await runner._handle_adapter_fatal_error_impl(adapter)

    runner._safe_adapter_disconnect.assert_awaited_once_with(
        adapter, Platform.TELEGRAM
    )
    getattr(runner, "request_restart").assert_called_once_with(
        detached=False, via_service=True
    )
    assert runner.adapters == {Platform.TELEGRAM: adapter}
    assert Platform.TELEGRAM not in runner._failed_platforms


@pytest.mark.asyncio
async def test_primary_rechecks_owner_fence_set_during_disconnect(tmp_path):
    """A normal coroutine return can still carry indeterminate ownership."""
    runner = _owner_fatal_runner(tmp_path)
    adapter = _make_adapter()
    adapter._set_fatal_error("telegram_network_error", "network", retryable=True)

    async def _disconnect_sets_owner_fence():
        adapter._polling_teardown_started = True
        adapter._set_fatal_error(
            "telegram_polling_owner_unterminated", "disconnect lost owner", retryable=True
        )

    adapter.disconnect = AsyncMock(side_effect=_disconnect_sets_owner_fence)
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.delivery_router.adapters = runner.adapters

    await runner._handle_adapter_fatal_error_impl(adapter)

    getattr(runner, "request_restart").assert_called_once_with(
        detached=False, via_service=True
    )
    assert runner.adapters == {Platform.TELEGRAM: adapter}
    assert Platform.TELEGRAM not in runner._failed_platforms


@pytest.mark.asyncio
async def test_safe_disconnect_globally_fences_future_telegram_creation(tmp_path):
    """Every cleanup caller inherits one ownership-aware replacement barrier."""
    runner = _owner_fatal_runner(tmp_path)
    adapter = _make_adapter()

    async def _disconnect_sets_owner_fence():
        adapter._polling_teardown_started = True
        adapter._set_fatal_error(
            "telegram_polling_owner_unterminated", "disconnect lost owner", retryable=True
        )

    adapter.disconnect = AsyncMock(side_effect=_disconnect_sets_owner_fence)

    completed = await runner._safe_adapter_disconnect(adapter, Platform.TELEGRAM)

    assert completed is False
    getattr(runner, "request_restart").assert_called_once_with(
        detached=False, via_service=True
    )
    from gateway.platform_registry import platform_registry

    replacement = MagicMock()
    with patch.object(platform_registry, "is_registered", return_value=True), patch.object(
        platform_registry, "create_adapter", return_value=replacement
    ) as create_adapter:
        created = runner._create_adapter(
            Platform.TELEGRAM, PlatformConfig(enabled=True, token="test-token")
        )

    assert created is None
    create_adapter.assert_not_called()
