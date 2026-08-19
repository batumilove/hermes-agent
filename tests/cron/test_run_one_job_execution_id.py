"""Tier 2: hermes.cron.* ActiveGraph events must carry execution_id.

The canonical executions DB (cron/executions.db) is the source of truth; the
ActiveGraph events carry only job_id today, forcing the evidence collector to
proximity-pair events to canonical rows across interval boundaries (lag up to
~16s observed). Adding the canonical execution id to the started/completed/
failed payloads makes per-execution parity exact.
"""
import cron.scheduler as s


def _base_patches(monkeypatch, events):
    monkeypatch.setattr(s, "_ag_emit", lambda t, p: events.append((t, p)))
    monkeypatch.setattr(s, "_ag_import_attempted", True)
    monkeypatch.setattr(s, "create_execution", lambda *a, **k: {"id": "exec-tier2-1"})
    monkeypatch.setattr(s, "claim_dispatch", lambda _jid: True)
    monkeypatch.setattr(s, "mark_execution_running", lambda _eid: None)
    monkeypatch.setattr(
        s, "run_job", lambda job, *, defer_agent_teardown=None, **kw: (True, "out", "final", None)
    )
    monkeypatch.setattr(s, "save_job_output", lambda jid, out: f"/tmp/{jid}.md")
    monkeypatch.setattr(s, "_deliver_result", lambda *a, **k: None)
    monkeypatch.setattr(s, "mark_job_run", lambda *a, **k: None)


def test_started_and_completed_carry_execution_id(monkeypatch):
    events = []
    _base_patches(monkeypatch, events)

    ok = s.run_one_job({"id": "job-tier2", "name": "tier2 job"})

    assert ok is True
    types = [t for t, _ in events]
    assert types == ["hermes.cron.started", "hermes.cron.completed"]
    for t, payload in events:
        assert payload.get("execution_id") == "exec-tier2-1", f"{t} missing execution_id"


def test_failed_terminal_carries_execution_id(monkeypatch):
    events = []
    _base_patches(monkeypatch, events)
    monkeypatch.setattr(
        s, "run_job", lambda job, *, defer_agent_teardown=None, **kw: (False, "", "", "boom")
    )

    ok = s.run_one_job({"id": "job-tier2", "name": "tier2 job"})

    assert ok is True  # failure handled, not raised
    terminals = [p for t, p in events if t in ("hermes.cron.completed", "hermes.cron.failed")]
    assert terminals and all(
        p.get("execution_id") == "exec-tier2-1" for p in terminals
    ), "terminal event missing execution_id"


def test_exception_path_failed_event_carries_execution_id(monkeypatch):
    events = []
    _base_patches(monkeypatch, events)

    def explode(*_a, **_k):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(s, "run_job", explode)

    ok = s.run_one_job({"id": "job-tier2", "name": "tier2 job"})

    assert ok is False
    failed = [p for t, p in events if t == "hermes.cron.failed"]
    assert failed and failed[0].get("execution_id") == "exec-tier2-1"
