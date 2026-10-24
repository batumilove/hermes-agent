"""Owner-side cron gates must not acquire a per-run session-store reference.

The daemon already owns state.db. Wake, injection and empty-prompt gates must
return before the execution borrows that store; an agent run must acquire and
release exactly one reference to the owner's existing store.
"""

from unittest.mock import MagicMock, patch
from tests.cron.test_cleanup_timeout import _owner_execution

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


class TestGateShortCircuitsNeverAcquireSessionDB:
    """Early gates run before acquisition, so no borrowed reference is stranded."""

    def _run(self, job, tmp_path):
        constructed = []
        owner_db = MagicMock()
        owner_db.db_path = tmp_path / "state.db"

        def _track(*args, **kwargs):
            db = owner_db
            constructed.append(db)
            return db

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("cron.scheduler_delivery._resolve_origin", return_value=None), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state_registry.acquire", side_effect=_track), \
             patch("run_agent.AIAgent") as agent_cls:
            agent_cls.return_value.run_conversation.return_value = {
                "final_response": "ok"
            }
            with _owner_execution(owner_db, job):
                result = run_job(job)
        return result, constructed

    def test_wake_gate_silent_skip_never_acquires_session_db(
        self, tmp_path, _stub_runtime_provider
    ):
        """Silent watchdog ticks must not acquire a per-run store reference."""
        from cron import scheduler

        with patch.object(
            scheduler,
            "_run_job_script_with_claim_heartbeat",
            return_value=(True, '{"wakeAgent": false}'),
        ):
            (success, _doc, final, _err), constructed = self._run(
                _make_job("wake-gate"), tmp_path
            )

        assert success is True
        assert final == SILENT_MARKER
        assert constructed == [], (
            "wake-gate silent skip must not acquire the owner-store reference — "
            "this return path has no acquired reference to release"
        )

    def test_injection_blocked_return_never_acquires_session_db(
        self, tmp_path, _stub_runtime_provider
    ):
        from cron import scheduler

        with patch.object(
            scheduler,
            "_run_job_script_with_claim_heartbeat",
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
            (success, doc, _final, _err), constructed = self._run(
                _make_job("injection-blocked"), tmp_path
            )

        assert success is False
        assert "blocked by prompt-injection scanner" in doc.lower() or "BLOCKED" in doc
        assert constructed == [], (
            "injection-blocked return must not acquire the owner-store reference — "
            "this return path has no acquired reference to release"
        )

    def test_empty_prompt_return_never_acquires_session_db(
        self, tmp_path, _stub_runtime_provider
    ):
        from cron import scheduler

        with patch.object(
            scheduler,
            "_run_job_script_with_claim_heartbeat",
            return_value=(True, ""),
        ), patch.object(scheduler, "_build_job_prompt", return_value=None):
            (success, _doc, final, _err), constructed = self._run(
                _make_job("empty-prompt"), tmp_path
            )

        assert success is True
        assert final == SILENT_MARKER
        assert constructed == [], (
            "empty-prompt return must not acquire the owner-store reference — "
            "this return path has no acquired reference to release"
        )


class TestAgentPathStillOwnsSessionDB:
    """Agent execution borrows the canonical store and releases its reference once."""

    def test_agent_run_acquires_and_releases_owner_store(
        self, tmp_path, _stub_runtime_provider
    ):
        from cron import scheduler

        job = _make_job("agent-path")
        job.pop("script")
        constructed = []
        owner_db = MagicMock()
        owner_db.db_path = tmp_path / "state.db"

        def _track(*args, **kwargs):
            db = owner_db
            db.get_compression_tip.return_value = None
            constructed.append(db)
            return db

        with patch("cron.scheduler._hermes_home", tmp_path), \
             patch("cron.scheduler_delivery._resolve_origin", return_value=None), \
             patch("hermes_cli.env_loader.load_hermes_dotenv"), \
             patch("hermes_cli.env_loader.reset_secret_source_cache"), \
             patch("hermes_state_registry.acquire", side_effect=_track), \
             patch("run_agent.AIAgent") as agent_cls:
            agent = MagicMock()
            agent.run_conversation.return_value = {"final_response": "ok"}
            agent_cls.return_value = agent

            with _owner_execution(owner_db, job):
                success, _doc, final, _err = run_job(job)

        assert success is True
        assert final == "ok"
        assert len(constructed) == 1, "agent path acquires exactly one owner-store reference"
        assert agent_cls.call_args.kwargs["session_db"] is not None
        constructed[0].close.assert_called_once()
