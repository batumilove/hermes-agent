"""Characterization + unit tests for the `run_one_job` shared helper (Phase 4A).

`tick`'s per-job body (`_process_job`) is the execute → save → deliver → mark
sequence that fires ONE due job. Phase 4A extracts it into a module-level
`run_one_job(job, *, adapters=None, loop=None, verbose=False)` so the external
Chronos provider's `fire_due` can reuse the IDENTICAL body — no duplicated
correctness.

The first test characterizes the sequence as driven through `tick()` (proving
the extraction didn't change `tick`'s behavior); the rest unit-test the
extracted helper directly.
"""
import pytest

import cron.scheduler as s


def test_ag_discovers_directory_plugin_before_giving_up(monkeypatch):
    """Cron ActiveGraph emission discovers directory plugins if the namespace is cold."""
    events = []
    imports = []
    discovered = []

    class FakePlugin:
        @staticmethod
        def _emit(event_type, payload):
            events.append((event_type, payload))

    def fake_import_module(name):
        imports.append(name)
        if len(imports) == 1:
            raise ModuleNotFoundError("No module named 'hermes_plugins'")
        return FakePlugin

    def fake_discover_plugins():
        discovered.append(True)

    import importlib
    import hermes_cli.plugins as plugins

    monkeypatch.setattr(s, "_ag_emit", None)
    monkeypatch.setattr(s, "_ag_import_attempted", False)
    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    monkeypatch.setattr(plugins, "discover_plugins", fake_discover_plugins)

    s._ag("hermes.cron.started", {"job_id": "cold-plugin"})

    assert discovered == [True]
    assert imports == ["hermes_plugins.activegraph", "hermes_plugins.activegraph"]
    assert events == [("hermes.cron.started", {"job_id": "cold-plugin"})]


def test_run_one_job_emits_activegraph_cron_events(monkeypatch):
    """Cron emits ActiveGraph started/completed events from the shared fire path."""
    events = []
    monkeypatch.setattr(s, "_ag_emit", lambda event_type, payload: events.append((event_type, payload)))
    monkeypatch.setattr(s, "_ag_import_attempted", True)
    monkeypatch.setattr(s, "claim_dispatch", lambda jid: True)
    monkeypatch.setattr(s, "run_job", lambda job, *, defer_agent_teardown=None: (True, "out", "final", None))
    monkeypatch.setattr(s, "save_job_output", lambda jid, out: f"/tmp/{jid}.md")
    monkeypatch.setattr(s, "_deliver_result", lambda *a, **k: None)
    monkeypatch.setattr(s, "mark_job_run", lambda *a, **k: None)

    ok = s.run_one_job({
        "id": "cron-ag-1",
        "name": "cron AG",
        "schedule": {"kind": "interval", "minutes": 5},
        "no_agent": True,
        "script": "watchdog.sh",
    })

    assert ok is True
    assert [event for event, _ in events] == ["hermes.cron.started", "hermes.cron.completed"]
    assert events[0][1] == {
        "job_id": "cron-ag-1",
        "job_name": "cron AG",
        "schedule": "every 5m",
        "no_agent": True,
        "has_script": True,
    }
    assert events[1][1]["job_id"] == "cron-ag-1"
    assert events[1][1]["success"] is True
    assert events[1][1]["output_len"] == 3
    assert events[1][1]["response_len"] == 5


@pytest.mark.parametrize("failing_bookkeeper", ["mark_job_run", "finish_execution"])
def test_run_one_job_emits_exactly_one_terminal_event_when_bookkeeping_fails(
    monkeypatch, failing_bookkeeper
):
    """A post-run bookkeeping error must not emit both completed and failed."""
    events = []
    monkeypatch.setattr(s, "_ag_emit", lambda event_type, payload: events.append((event_type, payload)))
    monkeypatch.setattr(s, "_ag_import_attempted", True)
    monkeypatch.setattr(s, "create_execution", lambda *a, **k: {"id": "exec-ag-bookkeeping"})
    monkeypatch.setattr(s, "claim_dispatch", lambda _jid: True)
    monkeypatch.setattr(s, "mark_execution_running", lambda _execution_id: None)
    monkeypatch.setattr(s, "run_job", lambda job, *, defer_agent_teardown=None: (True, "out", "final", None))
    monkeypatch.setattr(s, "save_job_output", lambda jid, out: f"/tmp/{jid}.md")
    monkeypatch.setattr(s, "_deliver_result", lambda *a, **k: None)

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"{failing_bookkeeper} failed")

    monkeypatch.setattr(s, "mark_job_run", fail if failing_bookkeeper == "mark_job_run" else lambda *a, **k: None)
    monkeypatch.setattr(s, "finish_execution", fail if failing_bookkeeper == "finish_execution" else lambda *a, **k: None)

    ok = s.run_one_job({"id": "cron-ag-bookkeeping", "name": "cron AG bookkeeping"})

    assert ok is False
    assert [event for event, _ in events] == ["hermes.cron.started", "hermes.cron.failed"]


def _patch_pipeline(monkeypatch, *, success=True, output="out", final="final response",
                    error=None, silent_marker_in=None):
    """Patch the job pipeline primitives and record the call order."""
    calls = []

    def fake_run_job(job, *, defer_agent_teardown=None):
        calls.append(("run_job", job["id"]))
        fr = final if silent_marker_in is None else silent_marker_in
        return (success, output, fr, error)

    def fake_save(jid, out):
        calls.append(("save", jid))
        return f"/tmp/{jid}.txt"

    def fake_deliver(job, content, adapters=None, loop=None):
        calls.append(("deliver", job["id"]))
        return None

    def fake_mark(jid, ok, err=None, delivery_error=None):
        calls.append(("mark", jid, ok))

    monkeypatch.setattr(s, "run_job", fake_run_job)
    monkeypatch.setattr(s, "save_job_output", fake_save)
    monkeypatch.setattr(s, "_deliver_result", fake_deliver)
    monkeypatch.setattr(s, "mark_job_run", fake_mark)
    return calls


def test_tick_process_job_sequence(monkeypatch):
    """Characterization: a single due job driven through tick() runs the
    sequence run_job → save → deliver → mark, in that order."""
    calls = _patch_pipeline(monkeypatch)
    monkeypatch.setattr(s, "get_due_jobs", lambda: [{"id": "j1", "name": "t"}])
    monkeypatch.setattr(s, "advance_next_runs", lambda ids: 1)

    s.tick(verbose=False, sync=True)

    assert [c[0] for c in calls] == ["run_job", "save", "deliver", "mark"]
    assert calls[-1] == ("mark", "j1", True)


def test_run_one_job_success_sequence(monkeypatch):
    """The extracted helper runs the same execute→save→deliver→mark sequence
    for a successful job."""
    calls = _patch_pipeline(monkeypatch)

    ok = s.run_one_job({"id": "j2", "name": "t"})

    assert ok is True
    assert [c[0] for c in calls] == ["run_job", "save", "deliver", "mark"]
    assert calls[-1] == ("mark", "j2", True)


def test_run_one_job_installs_secret_scope_under_multiplex(monkeypatch, tmp_path):
    """Regression: under profile isolation (multiplex active), run_one_job must
    execute run_job inside a profile secret scope so credential reads
    (resolve_runtime_provider -> get_secret) don't fail-close with
    UnscopedSecretError, and must tear the scope down afterward.

    Behavior contract: a scope is present during run_job and absent after,
    regardless of the concrete secret values.
    """
    from agent import secret_scope as ss

    # Point cron's home resolution at a profile whose .env carries a secret.
    (tmp_path / ".env").write_text("OPENROUTER_BASE_URL=https://openrouter.ai/api/v1\n")
    monkeypatch.setattr(s, "_get_hermes_home", lambda: tmp_path)

    scope_during_run = {}

    def fake_run_job(job, *, defer_agent_teardown=None):
        # This is where resolve_runtime_provider() would read a secret. Prove a
        # scope is installed and the profile's secret resolves without raising.
        scope_during_run["scope"] = ss.current_secret_scope()
        scope_during_run["base_url"] = ss.get_secret("OPENROUTER_BASE_URL")
        return (True, "out", "final", None)

    monkeypatch.setattr(s, "run_job", fake_run_job)
    monkeypatch.setattr(s, "save_job_output", lambda jid, out: f"/tmp/{jid}.txt")
    monkeypatch.setattr(s, "_deliver_result", lambda *a, **k: None)
    monkeypatch.setattr(s, "mark_job_run", lambda *a, **k: None)

    ss.set_multiplex_active(True)
    try:
        ok = s.run_one_job({"id": "j7", "name": "t"})
    finally:
        ss.set_multiplex_active(False)

    assert ok is True
    # Scope was installed during run_job and the profile secret resolved.
    assert scope_during_run["scope"] is not None
    assert scope_during_run["base_url"] == "https://openrouter.ai/api/v1"
    # And it was torn down after run_one_job returned (no leak).
    assert ss.current_secret_scope() is None


