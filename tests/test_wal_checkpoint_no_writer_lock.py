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
import gc
import threading
import time
import weakref
from unittest import mock

import pytest

import hermes_state
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
    completed = threading.Event()

    def counting_checkpoint():
        with lock:
            calls.append(time.monotonic())
        completed.set()

    monkeypatch.setattr(
        db, "_checkpoint_now", counting_checkpoint, raising=False
    )
    interval = db._CHECKPOINT_MIN_INTERVAL_S
    assert interval >= 30, "throttle window must be meaningfully large"
    for _ in range(4):
        db._execute_write(lambda conn: None, operation="burst_write")
    assert completed.wait(timeout=5), "scheduled checkpoint never ran"
    worker = db._checkpoint_worker
    if worker is not None:
        worker.join(timeout=5)
    assert len(calls) == 1, (
        f"burst of 4 counter-firing writes produced {len(calls)} "
        "checkpoints; throttle missing"
    )


def test_checkpoint_worker_is_one_shot_and_closed_db_is_collectible(tmp_path):
    handle = SessionDB(tmp_path / "collectible.db")
    handle._CHECKPOINT_MIN_INTERVAL_S = 0.0
    entered = threading.Event()
    release = threading.Event()

    def checkpoint():
        entered.set()
        release.wait(timeout=10)

    handle._checkpoint_now = checkpoint
    handle._try_wal_checkpoint()
    worker = handle._checkpoint_worker
    assert worker is not None
    assert entered.wait(timeout=5)
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()

    ref = weakref.ref(handle)
    handle.close()
    del handle
    gc.collect()
    assert ref() is None, "retired checkpoint worker retained closed SessionDB"


def test_close_does_not_wait_for_inflight_checkpoint(tmp_path, monkeypatch):
    handle = SessionDB(tmp_path / "close-nonblocking.db")
    monkeypatch.setattr(handle, "_CHECKPOINT_MIN_INTERVAL_S", 0.0)
    entered = threading.Event()
    release = threading.Event()

    def slow_checkpoint():
        entered.set()
        release.wait(timeout=10)

    monkeypatch.setattr(handle, "_checkpoint_now", slow_checkpoint, raising=False)
    handle._try_wal_checkpoint()
    worker = handle._checkpoint_worker
    assert worker is not None and entered.wait(timeout=5)

    started = time.monotonic()
    handle.close()
    assert time.monotonic() - started < 0.5
    assert worker.is_alive()
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()


def test_simultaneous_checkpoint_scheduling_starts_one_worker(db, monkeypatch):
    """Force both callers through the old unsynchronised worker check."""
    monkeypatch.setattr(db, "_CHECKPOINT_MIN_INTERVAL_S", 0.0)
    rendezvous = threading.Barrier(2)
    spawned = []

    class ExistingDeadWorker:
        def is_alive(self):
            try:
                rendezvous.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
            return False

    class SpawnedWorker:
        def __init__(self, *args, **kwargs):
            self.alive = False
            spawned.append(self)

        def start(self):
            self.alive = True

        def is_alive(self):
            return self.alive

    db._checkpoint_worker = ExistingDeadWorker()
    # Construct real caller threads before replacing the Thread factory used
    # inside _try_wal_checkpoint.
    callers = [threading.Thread(target=db._try_wal_checkpoint, daemon=True) for _ in range(2)]
    monkeypatch.setattr(hermes_state.threading, "Thread", SpawnedWorker)
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=5)
        assert not caller.is_alive()

    assert len(spawned) == 1


def test_checkpoint_thread_start_failure_never_runs_synchronously(db, monkeypatch, caplog):
    called = False

    def checkpoint():
        nonlocal called
        called = True

    class BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread exhausted")

    monkeypatch.setattr(db, "_checkpoint_now", checkpoint, raising=False)
    monkeypatch.setattr(hermes_state.threading, "Thread", BrokenThread)
    started = time.monotonic()
    db._try_wal_checkpoint()
    assert time.monotonic() - started < 0.5
    assert called is False
    assert db._checkpoint_worker is None
    assert "WAL checkpoint worker start failed" in caplog.text


def test_checkpoint_cadence_accounting_occurs_under_writer_lock(db, monkeypatch):
    lock_owned_during_set = []

    class WriteCountProbe:
        def __get__(self, instance, owner):
            if instance is None:
                return self
            return instance.__dict__["_write_count"]

        def __set__(self, instance, value):
            lock_owned_during_set.append(instance._lock._raw.locked())
            instance.__dict__["_write_count"] = value

    monkeypatch.setattr(SessionDB, "_write_count", WriteCountProbe(), raising=False)
    db._execute_write(lambda conn: None, operation="cadence_lock_probe")
    assert lock_owned_during_set == [True]


def test_write_count_accounting_is_serialized(db, monkeypatch):
    monkeypatch.setattr(db, "_CHECKPOINT_EVERY_N_WRITES", 10_000)
    callers = [
        threading.Thread(
            target=lambda: db._execute_write(lambda conn: None, operation="counted"),
            daemon=True,
        )
        for _ in range(20)
    ]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=5)
        assert not caller.is_alive()
    assert db._write_count == 20
