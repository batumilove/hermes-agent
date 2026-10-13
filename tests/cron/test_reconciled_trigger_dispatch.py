"""Reconciled trigger provenance through the real jobs store and execution ledger."""
from cron import executions, jobs, scheduler
from tools.cronjob_tools import _execute_job_now


def test_real_manual_script_run_publishes_terminal_provenance(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    scripts = home / "scripts"
    scripts.mkdir()
    script = scripts / "collector.py"
    script.write_text('print("collector result")\n')
    monkeypatch.setattr(scheduler, "_launch_external_cron_worker", lambda _job: False)
    job = jobs.create_job(
        prompt="", schedule="every 1h", script=str(script), no_agent=True, deliver="local",
    )
    result = _execute_job_now(job)
    assert result["success"] is True, result
    row = executions.latest_execution(job["id"])
    assert row["trigger_origin"] == "manual"
    assert row["scheduled_for"] is None
    assert row["status"] == "completed"
    assert not scheduler.is_job_running(job["id"])
    assert jobs.get_job(job["id"])["last_status"] == "ok"
