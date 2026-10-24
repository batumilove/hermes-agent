"""Observer failures must not change ownership or suppress recovered terminal rows."""
import sqlite3
import subprocess
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("kind", [sqlite3.ProgrammingError, sqlite3.InterfaceError])
def test_optional_observation_propagates_sqlite_api_errors(monkeypatch, kind):
    from cron import scheduler
    process = Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("worker", 1), 0]
    if kind is sqlite3.ProgrammingError:
        def read(_):
            with sqlite3.connect(":memory:") as conn:
                conn.execute("SELECT ?", ())  # genuine incorrect bindings
    else:
        def read(_):
            raise kind("invalid interface use")
    monkeypatch.setattr(scheduler, "get_execution", read)
    with pytest.raises(scheduler._ExternalWorkerPostHandoffError) as raised:
        scheduler._wait_for_external_cron_worker(process, execution_id="owned")
    assert isinstance(raised.value.__cause__, kind)
    assert process.wait.call_count == 1


def test_busy_then_terminal_releases_still_live_worker(monkeypatch):
    from cron import scheduler
    from cron import scheduler_detached_worker
    clock = [0.0]
    process = Mock()
    def wait(timeout):
        clock[0] += timeout
        if clock[0] > 20:
            raise AssertionError("terminal observation never resumed")
        raise subprocess.TimeoutExpired("worker", timeout)
    process.wait.side_effect = wait
    process.poll.return_value = None
    monkeypatch.setattr(scheduler.time, "monotonic", lambda: clock[0])
    read = Mock(side_effect=[sqlite3.OperationalError("database is locked"), {"status": "unknown"}])
    monkeypatch.setattr(scheduler, "get_execution", read)
    reap = Mock()
    monkeypatch.setattr(scheduler_detached_worker, "reap_terminal_worker_in_background", reap)
    assert scheduler._wait_for_external_cron_worker(process, execution_id="owned")
    assert read.call_count == 2
    assert 2 < process.wait.call_count <= 12  # bounded backoff, not tight retries
    reap.assert_called_once_with(process)


def test_recovered_terminal_real_live_child_is_background_reaped(tmp_path, monkeypatch):
    import sys
    from cron import executions, scheduler, scheduler_detached_worker
    monkeypatch.setattr(executions, "EXECUTIONS_FILE", tmp_path / "ledger.db")
    row = executions.create_execution("terminal-recovery", source="direct")
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(9)"])
    real_read = executions.get_execution
    observed = []
    reaped = []
    original_reap = scheduler_detached_worker.reap_terminal_worker_in_background
    def reap(child):
        assert child.poll() is None
        reaped.append(child.pid)
        original_reap(child)
    monkeypatch.setattr(scheduler_detached_worker, "reap_terminal_worker_in_background", reap)
    def read(eid):
        if not observed:
            observed.append(True)
            # Model the recovery writer's terminal outcome, not child death.
            with sqlite3.connect(executions.EXECUTIONS_FILE) as conn:
                conn.execute("UPDATE executions SET status='unknown' WHERE id=?", (eid,))
            raise sqlite3.OperationalError("database is locked")
        return real_read(eid)
    monkeypatch.setattr(scheduler, "get_execution", read)
    try:
        assert scheduler._wait_for_external_cron_worker(process, execution_id=row['id'])
        assert reaped == [process.pid]
        assert process.poll() is None
    finally:
        process.wait(timeout=15)
    assert process.returncode == 0
