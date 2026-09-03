"""P0-C: gate short-circuits must never strand the per-job SessionDB.

Inventory (t_00e4c7d2, §4): ``run_job`` opened the per-job SessionDB BEFORE the
three early-return gates — wake-gate silent skip, prompt-injection block, and
empty-prompt skip — so every silent tick stranded a writable handle with no
deterministic close (GC reclaimed the connection, but the PASSIVE WAL
checkpoint at close was skipped and correctness relied on refcounting). With
~45 script-gated watchdogs on a 5–30 min cadence, the wake-gate path alone
could strand dozens of handles per hour against the hot shared state.db.

Contract pinned here: the gates run BEFORE the per-job SessionDB is opened, so
a gate short-circuit never constructs (let alone leaks) a session store. A
cheap silent tick costs zero opens of state.db.
"""

from unittest.mock import MagicMock, patch

import pytest

from cron.scheduler import CronPromptInjectionBlocked, SILENT_MARKER, run_job

_RUNTIME = {
    "provider": "openrouter",
    "api_mode": "chat_completions",
    "base_url": "https://example.invalid/v1",
    "api_key": "test-key",
    "source": "stub",
    "requested_provider": None,
}


def _make_job(name="gate-leak-test", script="check.py"):
    return {
        "id": f"job_{name}",
        "name": name,
        "prompt": "Do a thing",
        "schedule": "*/5 * * * *",
        "script": script,
    }


@pytest.fixture()
def _stub_runtime_provider():
    with patch(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        return_value=_RUNTIME,
    ):
        yield


class TestGateShortCircuitsNeverOpenSessionDB:
    """Every early-return gate between job start and the guarded try must fire
    BEFORE the per-job SessionDB open — otherwise the handle is stranded with
    no deterministic close (the big ``finally`` only owns exits after ``try``).
    """

    def _run(self, job, tmp_path):
        constructed = []

        def _track(*args, **kwargs):
            db = MagicMock()
            constructed.append(db)
            return db

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("cron.scheduler._resolve_origin", return_value=None), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.SessionDB", side_effect=_track), \
             patch("run_agent.AIAgent") as agent_cls:
            agent_cls.return_value.run_conversation.return_value = {
                "final_response": "ok"
            }
            result = run_job(job)
        return result, constructed

    def test_wake_gate_silent_skip_never_opens_session_db(
        self, tmp_path, _stub_runtime_provider
    ):
        """Highest-frequency offender: every silent watchdog tick returns at
        the wake gate — it must not construct a SessionDB at all."""
        import cron.scheduler as scheduler

        with patch.object(
            scheduler,
            "_run_job_script",
            return_value=(True, '{"wakeAgent": false}'),
        ):
            (success, doc, final, err), constructed = self._run(
                _make_job("wake-gate"), tmp_path
            )

        assert success is True
        assert final == SILENT_MARKER
        assert constructed == [], (
            "wake-gate silent skip must not open the per-job SessionDB — "
            "the handle has no deterministic close on this return path"
        )

    def test_injection_blocked_return_never_opens_session_db(
        self, tmp_path, _stub_runtime_provider
    ):
        import cron.scheduler as scheduler

        with patch.object(
            scheduler,
            "_run_job_script",
            return_value=(True, '{"wakeAgent": true}'),
        ), patch.object(
            scheduler,
            "_build_job_prompt",
            # Resolve the exception class from the SAME module object we are
            # patching, at call time: if an earlier test in the suite reloaded
            # cron.scheduler, a collection-time ``from cron.scheduler import
            # CronPromptInjectionBlocked`` binds the pre-reload class, whose
            # identity no longer matches run_job's ``except`` clause — the
            # raised exception then escapes uncaught.
            side_effect=scheduler.CronPromptInjectionBlocked("threat pattern"),
        ):
            (success, doc, final, err), constructed = self._run(
                _make_job("injection-blocked"), tmp_path
            )

        assert success is False
        assert "blocked by prompt-injection scanner" in doc.lower() or "BLOCKED" in doc
        assert constructed == [], (
            "injection-blocked return must not open the per-job SessionDB — "
            "the handle has no deterministic close on this return path"
        )

    def test_empty_prompt_return_never_opens_session_db(
        self, tmp_path, _stub_runtime_provider
    ):
        import cron.scheduler as scheduler

        with patch.object(
            scheduler,
            "_run_job_script",
            return_value=(True, ""),
        ), patch.object(scheduler, "_build_job_prompt", return_value=None):
            (success, doc, final, err), constructed = self._run(
                _make_job("empty-prompt"), tmp_path
            )

        assert success is True
        assert final == SILENT_MARKER
        assert constructed == [], (
            "empty-prompt return must not open the per-job SessionDB — "
            "the handle has no deterministic close on this return path"
        )


class TestAgentPathStillOwnsSessionDB:
    """The move must not change the agent path: a job that passes all gates
    still gets a per-job SessionDB, still hands it to the agent, and the
    guarded ``finally`` still closes it exactly once (leak-freedom contract).
    """

    def test_agent_run_opens_and_closes_session_db(
        self, tmp_path, _stub_runtime_provider
    ):
        import cron.scheduler as scheduler

        job = _make_job("agent-path")
        job.pop("script")
        constructed = []

        def _track(*args, **kwargs):
            db = MagicMock()
            db.get_compression_tip.return_value = None
            constructed.append(db)
            return db

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("cron.scheduler._resolve_origin", return_value=None), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state.SessionDB", side_effect=_track), \
             patch("run_agent.AIAgent") as agent_cls:
            agent = MagicMock()
            agent.run_conversation.return_value = {"final_response": "ok"}
            agent_cls.return_value = agent

            success, doc, final, err = run_job(job)

        assert success is True
        assert final == "ok"
        assert len(constructed) == 1, "agent path opens exactly one SessionDB"
        agent_cls.call_args.kwargs["session_db"] is not None
        constructed[0].close.assert_called_once()
