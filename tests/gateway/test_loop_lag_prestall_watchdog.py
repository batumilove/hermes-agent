import logging
import threading
import time

from gateway.loop_lag_watchdog import PrestallLoopLagWatchdog


def _watchdog(*, now, threshold=5.0, poll_interval=0.01, caplog=None):
    logger = logging.getLogger("gateway.loop_lag_prestall_test")
    return PrestallLoopLagWatchdog(
        threshold=threshold,
        poll_interval=poll_interval,
        logger=logger,
        time_fn=lambda: now[0],
        main_thread_ident=threading.main_thread().ident,
    )


def test_prestall_watchdog_reports_once_then_rearms_after_heartbeat(caplog):
    now = [100.0]
    watchdog = _watchdog(now=now)
    watchdog.beat()

    with caplog.at_level("WARNING", logger="gateway.loop_lag_prestall_test"):
        now[0] = 106.0
        assert watchdog.sample_once() is True
        now[0] = 112.0
        assert watchdog.sample_once() is False
        watchdog.beat()
        now[0] = 118.0
        assert watchdog.sample_once() is True

    assert caplog.text.count("Gateway event loop pre-stall") == 2


def test_prestall_watchdog_zero_threshold_is_disabled():
    now = [100.0]
    watchdog = _watchdog(now=now, threshold=0)

    assert watchdog.start() is False
    assert watchdog.thread is None
    assert watchdog.sample_once() is False


def test_prestall_watchdog_stack_output_is_bounded(monkeypatch, caplog):
    now = [100.0]
    watchdog = _watchdog(now=now)
    watchdog.beat()
    monkeypatch.setattr(
        watchdog,
        "_format_main_thread_stack",
        lambda: "x" * (watchdog.MAX_STACK_BYTES * 4),
    )

    with caplog.at_level("WARNING", logger="gateway.loop_lag_prestall_test"):
        now[0] = 106.0
        assert watchdog.sample_once() is True

    message = caplog.records[-1].getMessage()
    assert len(message.encode("utf-8")) <= watchdog.MAX_LOG_BYTES
    assert "pre-stall stack truncated" in message


def test_prestall_watchdog_start_stop_leaves_no_live_thread():
    now = [time.monotonic()]
    watchdog = _watchdog(now=now, threshold=60.0)

    assert watchdog.start() is True
    thread = watchdog.thread
    assert thread is not None
    assert thread.daemon is True
    assert thread.is_alive()

    assert watchdog.stop(timeout=1.0) is True
    assert not thread.is_alive()
    assert watchdog.thread is None
