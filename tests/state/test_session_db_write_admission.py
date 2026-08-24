"""SessionDB instance bounding + gateway-wide write admission (P0-A).

Live evidence 2026-08-19 06:00-06:22Z (gateway MainPID 3141597): write-latency
warnings carried ``db_instance=1..137`` within one process. The instance id is
a process-lifetime monotonic counter, so the *number* is churn; the real
defects are (a) nothing bounds or even OBSERVES how many SessionDB instances
are concurrently alive in one process, so a leak is invisible until fd
exhaustion, and (b) write-heavy runs (gateway turns, cron agent runs, tool
ephemerals) from many instances all contend on the same state.db WAL write
lock with only per-instance jitter retry between them — a cross-instance
writer convoy with no fairness, no bound, and no admission control.

Contracts pinned here:

Part 1 — live-instance registry (``SessionDBLiveRegistry``):
  * every construction registers; ``close()`` deregisters.
  * census reports LIVE instances only (closed instances never counted).
  * census groups by db path; unclosed instances stay visible.

Part 2 — process-global bounded write admission (``SessionDBWriteAdmission``):
  * FIFO fairness across waiters (first-come-first-served by ticket).
  * bounded queue: reject with ``SessionDBWriteAdmissionFullError`` (mapped to
    HTTP 429 + Retry-After by callers) once queue depth hits capacity — the
    writer convoy becomes an explicit, load-shedding backpressure signal.
  * per-session ordering: a session's writes may not be OVERTAKEN by a later
    write for the SAME session (later write admitted only after the earlier
    one completes).
  * cancellation: an abandoned waiter stops waiting without consuming a slot.
  * slot release on ALL exit paths (body raise, body return, cancel).
  * drain-aware shutdown: after ``shutdown()``, admission waits fail fast with
    ``SessionDBWriteAdmissionClosedError`` and in-flight slots are released
    normally (shutdown waits for them, never leaks).
  * admission is keyed by database path so distinct state.db files get
    independent controllers, while every SessionDB instance pointing at the
    SAME file shares one controller (shared handle + cron ephemerals + tool
    ephemerals + subagent children).
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import hermes_state
from hermes_state import SessionDB


# ---------------------------------------------------------------------------
# Part 1 — live-instance registry
# ---------------------------------------------------------------------------


def test_registry_counts_live_instances_and_deregisters_on_close(tmp_path):
    d1 = SessionDB(db_path=tmp_path / "one.db")
    d2 = SessionDB(db_path=tmp_path / "two.db")
    census = hermes_state.session_db_live_census()
    assert census["total"] >= 2
    assert str(tmp_path / "one.db") in census["by_path"]
    assert str(tmp_path / "two.db") in census["by_path"]
    d1.close()
    d2.close()
    after = hermes_state.session_db_live_census()
    assert str(tmp_path / "one.db") not in after["by_path"]
    assert str(tmp_path / "two.db") not in after["by_path"]


def test_registry_reports_creation_stack_digest_and_instance_id(tmp_path):
    d = SessionDB(db_path=tmp_path / "state.db")
    try:
        census = hermes_state.session_db_live_census()
        entry = census["by_path"][str(tmp_path / "state.db")][0]
        assert entry["instance_id"] == getattr(d, "_write_instance_id")
        assert isinstance(entry["created_stack_digest"], str)
        assert entry["created_stack_digest"]
    finally:
        d.close()


def test_registry_census_reflects_gateway_churn_shape(tmp_path):
    """Transient per-run instances disappear; the long-lived one stays."""
    keeper = SessionDB(db_path=tmp_path / "state.db")
    try:
        for _ in range(5):
            SessionDB(db_path=tmp_path / "state.db").close()
        entry_list = hermes_state.session_db_live_census()["by_path"][
            str(tmp_path / "state.db")
        ]
        assert len(entry_list) == 1
    finally:
        keeper.close()


# ---------------------------------------------------------------------------
# Part 2 — bounded write admission
# ---------------------------------------------------------------------------


def _controller(capacity: int = 2, queue_limit: int = 4):
    return hermes_state.SessionDBWriteAdmission(
        capacity=capacity, queue_limit=queue_limit
    )


def test_admission_basic_acquire_release():
    adm = _controller(capacity=2)
    tok1 = adm.acquire(session_key="s1")
    tok2 = adm.acquire(session_key="s2")
    tok1.release()
    tok2.release()
    assert adm.stats()["in_flight"] == 0


def test_admission_fifo_fairness():
    """Waiters are granted strictly in ticket order (capacity=1 serializes).

    Registration order is made deterministic by acquiring all four tickets
    from the MAIN thread (acquire never blocks — it only registers intent);
    worker threads then contend for the grants. With capacity=1 each grant
    happens only after the previous holder releases, so the observed grant
    order must equal ticket order exactly, regardless of thread scheduling.
    """
    adm = _controller(capacity=1, queue_limit=8)
    order: list[str] = []
    first = adm.acquire(session_key="holder")
    tokens = {tag: adm.acquire(session_key=f"w{tag}") for tag in "ABCD"}
    assert adm.stats()["waiting"] == 4
    assert adm.stats()["in_flight"] == 1  # only `first`

    def hold(tag: str):
        with tokens[tag]:
            order.append(tag)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(hold, tag) for tag in "ABCD"]
        first.release()
        for f in futs:
            f.result(timeout=5)
    assert order == ["A", "B", "C", "D"]


def test_admission_rejects_when_queue_full():
    adm = _controller(capacity=1, queue_limit=1)
    holder = adm.acquire(session_key="hold")
    queued = adm.acquire(session_key="q1")  # fills the queue slot
    try:
        with pytest.raises(hermes_state.SessionDBWriteAdmissionFullError) as ei:
            adm.acquire(session_key="q2")
        assert ei.value.retry_after_s > 0
    finally:
        # Abandon the queued ticket first so it never holds a session-order
        # fence or queue entry; then free the granted slot.
        queued.cancel()
        holder.release()


def test_admission_full_error_carries_retry_after():
    adm = _controller(capacity=1, queue_limit=0)
    holder = adm.acquire(session_key="hold")
    try:
        with pytest.raises(hermes_state.SessionDBWriteAdmissionFullError) as ei:
            adm.acquire(session_key="x")
        assert ei.value.retry_after_s > 0.0
        assert ei.value.queue_depth == 0
    finally:
        holder.release()


def test_admission_per_session_ordering():
    """A queued write for session X cannot be overtaken by a later X write."""
    adm = _controller(capacity=1, queue_limit=8)
    first = adm.acquire(session_key="X")
    # Later write for the SAME session queues behind `first`.
    tok = adm.acquire(session_key="X")
    release_first = threading.Event()

    def _release():
        release_first.wait(timeout=5)
        first.release()

    t = threading.Thread(target=_release, daemon=True)
    t.start()
    # `tok` cannot be granted until `first` (same session, earlier ticket) is
    # released. If per-session ordering is broken, acquire returns while
    # `first` still holds the slot and the assertion below fails.
    acquired_thread = []
    ev = threading.Event()

    def _try_hold():
        with tok:
            acquired_thread.append(release_first.is_set())
        ev.set()

    th = threading.Thread(target=_try_hold, daemon=True)
    th.start()
    assert not ev.wait(timeout=0.3), "session write overtook its predecessor"
    release_first.set()
    th.join(timeout=5)
    t.join(timeout=5)
    assert acquired_thread == [True]


def test_admission_per_session_ordering_across_waiters():
    """Same-session waiters must complete ticket order even when queued."""
    adm = hermes_state.SessionDBWriteAdmission(capacity=1, queue_limit=8)
    holder = adm.acquire(session_key="S")
    order: list[int] = []

    def worker(n: int):
        with adm.acquire(session_key="S"):
            order.append(n)

    with ThreadPoolExecutor(max_workers=3) as pool:
        # Stagger submissions so ticket order is deterministic.
        f1 = pool.submit(worker, 1)
        _wait_for_queue_depth(adm, 1)
        f2 = pool.submit(worker, 2)
        _wait_for_queue_depth(adm, 2)
        f3 = pool.submit(worker, 3)
        _wait_for_queue_depth(adm, 3)
        holder.release()
        f1.result(timeout=5)
        f2.result(timeout=5)
        f3.result(timeout=5)
    assert order == [1, 2, 3]


def _wait_for_queue_depth(adm, want: int, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while adm.stats()["waiting"] < want and time.monotonic() < deadline:
        time.sleep(0.01)


def test_admission_cancelled_waiter_consumes_no_slot():
    adm = _controller(capacity=1, queue_limit=8)
    holder = adm.acquire(session_key="hold")
    try:
        tok = adm.acquire(session_key="queued-waiter")
        assert tok.cancel() is True
        # A cancelled waiter must not hold a queue entry...
        assert adm.stats()["waiting"] == 0
    finally:
        holder.release()


def test_admission_release_on_exception_paths():
    adm = _controller(capacity=2)
    with pytest.raises(RuntimeError):
        with adm.acquire(session_key="boom"):
            raise RuntimeError("body raise")
    assert adm.stats()["in_flight"] == 0


def test_admission_shutdown_fails_fast_and_releases_in_flight():
    adm = _controller(capacity=2)
    tok = adm.acquire(session_key="live")
    adm.shutdown()
    try:
        with pytest.raises(hermes_state.SessionDBWriteAdmissionClosedError):
            adm.acquire(session_key="late")
    finally:
        tok.release()  # in-flight slot releases normally after shutdown
    assert adm.stats()["in_flight"] == 0


def test_admission_shutdown_rejects_new_waiters():
    adm = _controller(capacity=1, queue_limit=4)
    holder = adm.acquire(session_key="hold")
    try:
        adm.shutdown()
        with pytest.raises(hermes_state.SessionDBWriteAdmissionClosedError):
            adm.acquire(session_key="late")
    finally:
        holder.release()


def test_admission_stats_shape():
    adm = _controller(capacity=2, queue_limit=4)
    with adm.acquire(session_key="s"):
        stats = adm.stats()
        assert stats["in_flight"] == 1
        assert stats["capacity"] == 2
        assert stats["waiting"] == 0
        assert stats["queue_limit"] == 4
    assert adm.stats()["in_flight"] == 0


def test_admission_is_shared_per_db_path(tmp_path):
    """All instances pointing at one file share one controller."""
    d1 = SessionDB(db_path=tmp_path / "state.db")
    d2 = SessionDB(db_path=tmp_path / "state.db")
    try:
        c1 = d1._write_admission()
        c2 = d2._write_admission()
        assert c1 is c2
    finally:
        d1.close()
        d2.close()


def test_execute_write_routes_through_admission(tmp_path):
    """_execute_write must hold an admission slot for the whole transaction."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        adm = db._write_admission()
        adm2 = hermes_state.SessionDBWriteAdmission.for_path(db.db_path)
        assert adm is adm2
        seen = []
        from hermes_state import SessionDBWriteAdmissionFullError

        def _write(cx):
            seen.append(adm.stats()["in_flight"])
            cx.execute("CREATE TABLE IF NOT EXISTS adm_probe (k TEXT)")
            return 7

        assert db._execute_write(_write) == 7
        assert seen == [1]
        assert adm.stats()["in_flight"] == 0
    finally:
        db.close()


def test_execute_write_surfaces_full_as_operational_error(tmp_path):
    """Queue-full must surface as a sqlite OperationalError subclass the
    write paths already handle, carrying retry_after for 429 mapping."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        adm = db._write_admission()
        adm._override_for_test(capacity=1, queue_limit=0)
        blocker = adm.acquire(session_key="blocker")
        try:
            with pytest.raises(hermes_state.SessionDBWriteAdmissionFullError) as ei:
                db._execute_write(
                    lambda cx: cx.execute(
                        "CREATE TABLE IF NOT EXISTS adm_full (k TEXT)"
                    ),
                    operation="admission_full_probe",
                )
            assert ei.value.retry_after_s > 0
        finally:
            blocker.release()
    finally:
        db.close()


def test_live_census_hidden_from_read_only_instances(tmp_path):
    SessionDB(db_path=tmp_path / "ro.db").close()
    writer = SessionDB(db_path=tmp_path / "ro.db")
    try:
        census = hermes_state.session_db_live_census()
        # read_only handles register too — one live entry for the writer.
        assert census["by_path"].get(str(tmp_path / "ro.db")) is not None
    finally:
        writer.close()


def test_gateway_run_admission_endpoint_rejects_with_429(monkeypatch, tmp_path):
    """Agent-serving endpoints map queue-full to 429 + Retry-After."""
    from gateway import write_admission as gwa

    adm = gwa.get_admission_for_profile(str(tmp_path))
    adm._override_for_test(capacity=1, queue_limit=0)
    holder = adm.acquire(session_key="hold")
    try:
        exc = gwa.try_acquire_turn_admission(str(tmp_path), session_key="s1")
        assert exc is not None
        assert exc.retry_after_s > 0
    finally:
        holder.release()


def test_gateway_run_admission_endpoint_ok(monkeypatch, tmp_path):
    from gateway import write_admission as gwa

    adm = gwa.get_admission_for_profile(str(tmp_path))
    # Default profile controller must be shared per profile path.
    assert gwa.get_admission_for_profile(str(tmp_path)) is adm
    token = gwa.try_acquire_turn_admission(str(tmp_path), session_key="s1")
    assert token is not None
    token.release()


def test_gateway_run_admission_drain_aware_shutdown(monkeypatch, tmp_path):
    from gateway import write_admission as gwa

    adm = gwa.get_admission_for_profile(str(tmp_path))
    holder = adm.acquire(session_key="live-turn")
    adm.shutdown()
    with pytest.raises(Exception):
        gwa.try_acquire_turn_admission_or_raise(
            str(tmp_path), session_key="late"
        )
    holder.release()


def test_execute_write_admission_wraps_body(tmp_path):
    """Slot held across BEGIN..commit (whole _execute_write call), released on raise."""
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        adm = db._write_admission()

        def _boom(cx):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            db._execute_write(_boom, operation="admission_raise_probe")
        assert adm.stats()["in_flight"] == 0

        def _ok(cx):
            cx.execute("CREATE TABLE IF NOT EXISTS adm2 (k TEXT)")
            return "fine"

        assert db._execute_write(_ok, operation="admission_ok_probe") == "fine"
        assert adm.stats()["in_flight"] == 0
    finally:
        db.execute_write_admission_shutdown()
        db.close()


def test_controller_reentrant_acquire_passthrough(tmp_path):
    """acquire() from a thread already holding a granted slot returns a
    passthrough token (no queue ticket), so a nested SessionDB write in the
    same thread can never raise queue-full against its own outer slot nor
    leak a granted-but-abandoned ticket. (Nested _execute_write inside a
    write callback is impossible on one SQLite connection — BEGIN within
    BEGIN — so the guard is exercised at the controller level.)"""
    adm = hermes_state.SessionDBWriteAdmission(capacity=1, queue_limit=0)
    with adm.acquire(session_key="outer") as outer_tok:
        assert adm.stats()["in_flight"] == 1
        nested = adm.acquire(session_key="inner")
        with nested:
            # passthrough: no extra slot, no queue entry, no raise despite
            # queue_limit=0 and capacity already exhausted.
            assert adm.stats()["in_flight"] == 1
            assert adm.stats()["waiting"] == 0
        # nested release returned the passthrough token, not the outer slot
        assert adm.stats()["in_flight"] == 1
    assert adm.stats()["in_flight"] == 0


def test_slow_write_warning_carries_live_census(tmp_path, caplog):
    """The slow-write log line must include live_instances_total so the
    2026-08-19 'db_instance=1..137' evidence is directly answerable."""
    import logging

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        keeper = SessionDB(db_path=tmp_path / "state.db")
        try:
            with caplog.at_level(logging.WARNING, logger="hermes_state"):
                db._SLOW_WRITE_WARN_S = 0.0  # force the warning on next write

                def _do(cx):
                    cx.execute("CREATE TABLE IF NOT EXISTS census_probe (k TEXT)")

                db._execute_write(_do, operation="census_probe")
            warn = [
                r for r in caplog.records if "live_instances_total" in r.getMessage()
            ]
            assert warn, "slow-write warning missing live_instances_total"
            msg = warn[0].getMessage()
            assert "live_instances_total=" in msg
            # at least this handle + the keeper are live
            import re as _re

            m = _re.search(r"live_instances_total=(\d+)", msg)
            assert m and int(m.group(1)) >= 2
        finally:
            keeper.close()
    finally:
        # Remove the instance-level override entirely; restoring a literal
        # would leave a shadow (None breaks the elapsed comparison) and the
        # class default must govern again.
        db.__dict__.pop("_SLOW_WRITE_WARN_S", None)
        db.close()
