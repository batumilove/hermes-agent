"""A live worker, not a failed observer read, owns the fire claim."""
import sqlite3
import subprocess
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("error", [sqlite3.OperationalError("database is locked"), OSError("ledger unavailable")])
def test_live_worker_survives_monitor_read_failure(monkeypatch, error):
    from cron import scheduler

    committed = []
    process = Mock()
    def wait(timeout):
        if not committed:
            committed.append("side effect committed")
            raise subprocess.TimeoutExpired("worker", timeout)
        return 0
    process.wait.side_effect = wait
    process.poll.return_value = None
    read = Mock(side_effect=[error, {"status": "completed"}])
    monkeypatch.setattr(scheduler, "get_execution", read)
    finish = Mock()
    monkeypatch.setattr(scheduler, "terminalize_dead_owner", finish)
    assert scheduler._wait_for_external_cron_worker(process, execution_id="owned") is True
    assert committed == ["side effect committed"]
    assert process.wait.call_count == 2
    finish.assert_not_called()


def test_real_worker_commits_after_failed_monitor_read(tmp_path, monkeypatch):
    import sys
    from cron import scheduler

    committed = tmp_path / "committed"
    process = subprocess.Popen([
        sys.executable, "-c",
        "import pathlib,sys,time; time.sleep(1.4); pathlib.Path(sys.argv[1]).write_text('committed')",
        str(committed),
    ])
    reads = []
    def read(_execution_id):
        reads.append(process.poll())
        if len(reads) == 1:
            assert process.poll() is None
            raise sqlite3.OperationalError("database is locked")
        assert committed.read_text() == "committed"
        return {"status": "completed"}
    monkeypatch.setattr(scheduler, "get_execution", read)
    try:
        assert scheduler._wait_for_external_cron_worker(process, execution_id="owned")
        assert process.returncode == 0
        assert len(reads) == 2
    finally:
        # The synthetic child is finite even on RED; reap without signalling.
        process.wait(timeout=5)


def test_monitor_read_failure_does_not_mask_programming_error(monkeypatch):
    from cron import scheduler

    process = Mock()
    process.wait.side_effect = subprocess.TimeoutExpired("worker", 1)
    monkeypatch.setattr(scheduler, "get_execution", Mock(side_effect=ValueError("bad row")))
    with pytest.raises(scheduler._ExternalWorkerPostHandoffError, match="bad row"):
        scheduler._wait_for_external_cron_worker(process, execution_id="owned")


def test_monitor_read_failure_keeps_cancellation_visible(monkeypatch):
    from cron import scheduler

    process = Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("worker", 1), KeyboardInterrupt()]
    read = Mock(side_effect=sqlite3.OperationalError("database is locked"))
    monkeypatch.setattr(scheduler, "get_execution", read)
    with pytest.raises(KeyboardInterrupt):
        scheduler._wait_for_external_cron_worker(process, execution_id="owned")
    assert read.call_count == 1


def test_monitor_read_failure_backs_off_before_retry(monkeypatch):
    from cron import scheduler

    process = Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("worker", 1)] * 3 + [0]
    read = Mock(side_effect=[sqlite3.OperationalError("database is locked"), {"status": "completed"}])
    monkeypatch.setattr(scheduler, "get_execution", read)
    assert scheduler._wait_for_external_cron_worker(process, execution_id="owned")
    assert read.call_count == 2
    assert process.wait.call_count == 4


def test_dead_worker_read_failure_is_not_success(monkeypatch):
    from cron import scheduler

    process = Mock()
    process.wait.return_value = 9
    monkeypatch.setattr(scheduler, "get_execution", Mock(side_effect=OSError("ledger unavailable")))
    with pytest.raises(scheduler._ExternalWorkerPostHandoffError, match="ledger unavailable"):
        scheduler._wait_for_external_cron_worker(process, execution_id="owned")
