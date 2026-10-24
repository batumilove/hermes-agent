"""A rejected external occurrence is audited without changing its live owner."""

import sqlite3

import pytest

from cron import executions, jobs, scheduler
from cron.scheduler_provider import InProcessCronScheduler


@pytest.mark.parametrize("manual", [False, True], ids=["external", "manual"])
def test_rejected_fire_is_terminal_and_preserves_the_winners_claim(tmp_path, monkeypatch, manual):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    provider = InProcessCronScheduler()
    job = jobs.create_job(prompt="audit contention", schedule="every 1h", name="audit")
    winner = provider.claim_fire(job["id"])
    assert winner is not None
    before = jobs.get_job(job["id"])
    runs = []
    monkeypatch.setattr(scheduler, "run_one_job", lambda *args, **kwargs: runs.append(args))
    try:
        assert provider.fire_due(job["id"], manual=manual) is False
        rows = executions.list_executions(job_id=job["id"])
        assert len(rows) == 2
        accepted = next(row for row in rows if row["id"] == winner["execution_id"])
        rejected = next(row for row in rows if row["id"] != winner["execution_id"])
        assert accepted["status"] == "claimed"
        assert rejected["status"] == "failed" and rejected["finished_at"] is not None
        assert rejected["error"] == "Fire claim was not acquired"
        assert rejected["trigger_origin"] == ("manual" if manual else "external")
        assert rejected["source"] == provider.name
        assert rejected["scheduled_instant"] is None
        assert jobs.get_job(job["id"]) == before
        assert runs == []
    finally:
        jobs.release_fire_claim(job["id"], expected_owner=winner["fire_claim"]["by"])


def test_rejected_audit_write_failure_never_releases_the_winners_claim(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    provider = InProcessCronScheduler()
    job = jobs.create_job(prompt="audit persistence", schedule="every 1h", name="audit")
    winner = provider.claim_fire(job["id"])
    assert winner is not None
    before = jobs.get_job(job["id"])
    error = sqlite3.OperationalError("ledger unavailable")

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(executions, "create_execution", unavailable)
    try:
        with pytest.raises(sqlite3.OperationalError) as raised:
            provider.fire_due(job["id"])
        assert raised.value is error
        assert jobs.get_job(job["id"]) == before
        rows = executions.list_executions(job_id=job["id"])
        assert len(rows) == 1 and rows[0]["id"] == winner["execution_id"]
        assert rows[0]["status"] == "claimed"
    finally:
        jobs.release_fire_claim(job["id"], expected_owner=winner["fire_claim"]["by"])
