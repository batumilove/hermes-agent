"""Hostile tests: periodic WAL checkpoints must not convoy other writers.

Observed 2026-08-23 on the 9.3GB production state.db: the periodic
PASSIVE checkpoint ran while holding the SessionDB instance writer mutex
(holder_kind=maintenance), queueing every writer behind multi-second
checkpoint fsyncs (24-81s holds, queue_depth=10, writer waits 29-49s,
close hold 188.7s) and tripping the gateway loop-liveness watchdog
(exit 75, twice).

Correct invariants:
1. A concurrent writer must NOT be blocked behind an in-flight periodic
   checkpoint triggered by another writer (the convoy).
2. Checkpoints are time-throttled: a burst of writes past the counter
   threshold fires at most one checkpoint per throttle window.
"""
import threading
import time
from unittest import mock

import pytest

from hermes_state import SessionDB


@pytest.fixture
def db(tmp_path):
    handle = SessionDB(tmp_path / "state.db")
    yield handle
    handle.close()


def test_concurrent_writer_not_queued_behind_inflight_checkpoint(db, monkeypatch):
    monkeypatch.setattr(db, "_CHECKPOINT_EVERY_N_WRITES", 1)
    monkeypatch.setattr(db, "_CHECKPOINT_MIN_INTERVAL_S", 0.0)
    entered = threading.Event()
    release = threading.Event()

    def slow_checkpoint():
        entered.set()
        release.wait(timeout=10)

    monkeypatch.setattr(db, "_checkpoint_now", slow_checkpoint, raising=False)

    caller_done = threading.Event()
    other_done = threading.Event()

    def caller():
        db._execute_write(lambda conn: None, operation="checkpoint_caller")
        caller_done.set()

    def other_writer():
        db._execute_write(lambda conn: None, operation="concurrent_writer")
        other_done.set()

    t1 = threading.Thread(target=caller, daemon=True)
    t1.start()
    assert entered.wait(timeout=5), "checkpoint never ran"

    t2 = threading.Thread(target=other_writer, daemon=True)
    t2.start()
    # The convoy: t2 must complete while the checkpoint t1 triggered is
    # still in flight (t1 itself legitimately waits for its own call).
    assert other_done.wait(timeout=2), (
        "concurrent writer queued behind in-flight checkpoint: "
        "convoy reproduced"
    )
    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert caller_done.is_set()


def test_checkpoint_throttled_in_time(db, monkeypatch):
    monkeypatch.setattr(db, "_CHECKPOINT_EVERY_N_WRITES", 1)
    calls = []
    lock = threading.Lock()

    def counting_checkpoint():
        with lock:
            calls.append(time.monotonic())

    monkeypatch.setattr(
        db, "_checkpoint_now", counting_checkpoint, raising=False
    )
    interval = db._CHECKPOINT_MIN_INTERVAL_S
    assert interval >= 30, "throttle window must be meaningfully large"
    for _ in range(4):
        db._execute_write(lambda conn: None, operation="burst_write")
    assert len(calls) <= 1, (
        f"burst of 4 counter-firing writes produced {len(calls)} "
        "checkpoints; throttle missing"
    )
