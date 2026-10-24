"""Cron job editing, pause and resume contracts."""

import pytest
from datetime import datetime, timedelta, UTC
from cron.jobs import create_job, update_job, get_job, pause_job, resume_job, get_due_jobs, claim_job_for_fire, save_jobs, load_jobs, parse_schedule, _hermes_now

@pytest.fixture()
def tmp_cron_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cron.jobs.CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr("cron.jobs.JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr("cron.jobs.OUTPUT_DIR", tmp_path / "cron" / "output")
    return tmp_path


class TestUpdateJob:
    def test_update_name(self, tmp_cron_dir):
        job = create_job(prompt="Check server status", schedule="every 1h", name="Old Name")
        assert job["name"] == "Old Name"
        updated = update_job(job["id"], {"name": "New Name"})
        assert updated is not None
        assert isinstance(updated, dict)
        assert updated["name"] == "New Name"
        # Verify other fields are preserved
        assert updated["prompt"] == "Check server status"
        assert updated["id"] == job["id"]
        assert updated["schedule"] == job["schedule"]
        # Verify persisted to disk
        fetched = get_job(job["id"])
        assert fetched["name"] == "New Name"

    def test_rejects_update_that_combines_once_schedule_with_repeats(self, tmp_cron_dir):
        job = create_job(prompt="Poll repeatedly", schedule="every 10m", repeat=144)
        before = get_job(job["id"])

        with pytest.raises(ValueError, match="one-shot schedules run exactly once"):
            update_job(job["id"], {"schedule": parse_schedule("in 10m")})

        assert get_job(job["id"]) == before

    def test_rejects_repeat_increase_on_existing_once_schedule(self, tmp_cron_dir):
        job = create_job(prompt="Run later", schedule="in 10m")
        before = get_job(job["id"])
        assert before is not None
        repeat_state = {**before["repeat"], "times": 2}

        with pytest.raises(ValueError, match="one-shot schedules run exactly once"):
            update_job(job["id"], {"repeat": repeat_state})

        assert get_job(job["id"]) == before


class TestPauseResumeJob:
    def test_pause_sets_state(self, tmp_cron_dir):
        job = create_job(prompt="Pause me", schedule="every 1h")
        paused = pause_job(job["id"], reason="user paused")
        assert paused is not None
        assert paused["enabled"] is False
        assert paused["state"] == "paused"
        assert paused["paused_reason"] == "user paused"
        assert paused.get("paused_at")

    def test_pause_is_authoritative_due_jobs_do_not_fire(self, tmp_cron_dir):
        """Behavioural invariant: after pause, a past-due job must not be due.

        Checks that last_run_at cannot advance via the scheduler path — not
        merely that pause() returned success. Regression for the 07-30 outage
        where state=paused coexisted with enabled=true and jobs kept firing.
        """
        job = create_job(prompt="Must not fire while paused", schedule="every 1h")
        past = (_hermes_now() - timedelta(hours=2)).isoformat()
        # Force the job overdue, then pause.
        updated = update_job(job["id"], {"next_run_at": past})
        assert updated["enabled"] is True
        assert job["id"] in {j["id"] for j in get_due_jobs()}

        paused = pause_job(job["id"], reason="outage freeze")
        assert paused["enabled"] is False
        assert paused["state"] == "paused"
        assert paused.get("paused_at")
        # Scheduler-honoured flag and pause markers must never contradict.
        assert not (paused.get("enabled") and paused.get("paused_at"))

        due_ids = {j["id"] for j in get_due_jobs()}
        assert job["id"] not in due_ids

        before = get_job(job["id"])
        assert before["last_run_at"] is None or before["last_run_at"] == job.get("last_run_at")
        # claim path also closed
        assert claim_job_for_fire(job["id"]) is False
        after = get_job(job["id"])
        assert after["last_run_at"] == before.get("last_run_at")
        assert after["enabled"] is False

    def test_contradictory_half_pause_self_disables_and_does_not_fire(self, tmp_cron_dir):
        """enabled=true + paused_at must not fire; scan heals enabled=false."""
        now = _hermes_now()
        job = {
            "id": "half-paused-1",
            "name": "half-paused",
            "prompt": "should never run",
            "schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"},
            "schedule_display": "every 5m",
            "repeat": {"times": None, "completed": 0},
            # The contradiction from the 07-30 outage:
            "enabled": True,
            "state": "paused",
            "paused_at": (now - timedelta(hours=20)).isoformat(),
            "paused_reason": "operator thought this was frozen",
            "next_run_at": (now - timedelta(hours=1)).isoformat(),
            "last_run_at": None,
            "last_status": None,
            "last_error": None,
            "last_delivery_error": None,
            "created_at": (now - timedelta(days=1)).isoformat(),
            "deliver": "local",
        }
        save_jobs([job])

        # Display must NOT say paused while enabled (honest list).
        from cron.jobs import effective_job_state, list_jobs

        assert effective_job_state(job) == "scheduled"
        listed = {j["id"]: j for j in list_jobs(include_disabled=True)}
        # Honest list: enabled=true half-pause must not render as paused.
        assert listed["half-paused-1"]["enabled"] is True
        assert listed["half-paused-1"]["state"] != "paused"

        assert claim_job_for_fire("half-paused-1") is False
        due = get_due_jobs()
        assert "half-paused-1" not in {j["id"] for j in due}

        healed = get_job("half-paused-1")
        assert healed is not None
        assert healed["enabled"] is False
        assert healed["state"] == "paused"
        assert healed.get("paused_at")
        # Still not due after heal
        assert "half-paused-1" not in {j["id"] for j in get_due_jobs()}

    def test_resume_rejects_past_oneshot(self, tmp_cron_dir, monkeypatch):
        """Resuming a paused one-shot whose time is now in the past must raise
        ValueError — the revived job would silently never fire."""
        now = datetime(2026, 7, 6, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr("cron.jobs._hermes_now", lambda: now)
        # Create directly — bypass create_job's past-oneshot guard so we can
        # test the resume path independently.
        job = {
            "id": "test-resume-past",
            "name": "test-resume-past",
            "prompt": "Past one-shot",
            "schedule": {"kind": "once", "run_at": (now - timedelta(minutes=5)).isoformat(), "display": "once"},
            "repeat": {"times": 1, "completed": 0},
            "enabled": False,
            "state": "paused",
            "paused_at": now.isoformat(),
            "paused_reason": "test",
            "next_run_at": None,
            "last_run_at": None,
            "last_status": None,
            "last_error": None,
            "last_delivery_error": None,
            "created_at": (now - timedelta(hours=1)).isoformat(),
            "deliver": "local",
        }
        save_jobs([job])
        with pytest.raises(ValueError, match="in the past"):
            resume_job("test-resume-past")

    def test_resume_keeps_slot_that_elapsed_while_paused_due(self, tmp_cron_dir, monkeypatch):
        """A recurring job paused before its slot and resumed after it comes back with that slot
        still due — the due scan then fires it (late/catch-up) or logs the skip. Re-anchoring
        from now consumed the occurrence with no run, no ledger row and no log line (#113603)."""
        now = datetime(2026, 9, 16, 17, 0, 0, tzinfo=UTC)
        monkeypatch.setattr("cron.jobs._hermes_now", lambda: now)
        job = create_job(prompt="daily pipeline", schedule="30 1 * * *", deliver="local")
        stored = load_jobs()
        row = next(r for r in stored if r["id"] == job["id"])
        slot = datetime(2026, 9, 16, 1, 30, 0, tzinfo=UTC).isoformat()
        row["next_run_at"] = slot
        save_jobs(stored)

        pause_job(job["id"], reason="ops audit")
        assert job["id"] not in {j["id"] for j in get_due_jobs()}
        assert get_job(job["id"])["next_run_at"] == slot

        assert resume_job(job["id"])["next_run_at"] == slot
        assert job["id"] in {j["id"] for j in get_due_jobs()}

    def test_resume_recomputes_future_or_missing_slot_from_now(self, tmp_cron_dir, monkeypatch):
        """Control: a paused job whose stored slot is still ahead, or created ``--paused`` with no
        slot, resumes onto the next future occurrence as before."""
        now = datetime(2026, 9, 16, 17, 0, 0, tzinfo=UTC)
        monkeypatch.setattr("cron.jobs._hermes_now", lambda: now)
        ahead = create_job(prompt="daily", schedule="30 1 * * *", deliver="local")
        pause_job(ahead["id"])
        canary = create_job(prompt="canary", schedule="0 9 * * *", deliver="local", paused=True)
        assert get_job(canary["id"])["next_run_at"] is None

        for jid in (ahead["id"], canary["id"]):
            resumed = resume_job(jid)
            assert datetime.fromisoformat(resumed["next_run_at"]) > now
            assert jid not in {j["id"] for j in get_due_jobs()}
