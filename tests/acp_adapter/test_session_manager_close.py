"""P0-C: SessionManager must deterministically close its lazy SessionDB.

Inventory (t_00e4c7d2 §2e #21): ``acp_adapter/session.py`` lazily constructs a
per-manager ``SessionDB`` (``_db_instance``) and never closes it on session
end — the writable handle (and its skipped PASSIVE checkpoint at close) is
left to GC / process exit on every ACP server teardown.

Contract pinned here: ``SessionManager.close()`` closes a lazily-opened
instance exactly once, is idempotent, tolerates a failing ``close()``, and
resets the manager so a later ``_get_db()`` reopens fresh rather than reusing
a closed handle.
"""

from unittest.mock import MagicMock, patch

from acp_adapter.session import SessionManager


class TestSessionManagerClose:
    def test_close_closes_lazily_opened_db(self):
        mgr = SessionManager()
        db = MagicMock()
        with patch("hermes_state.SessionDB", return_value=db):
            mgr._get_db()
            mgr.close()

        db.close.assert_called_once()

    def test_close_is_idempotent(self):
        mgr = SessionManager()
        db = MagicMock()
        with patch("hermes_state.SessionDB", return_value=db):
            mgr._get_db()
            mgr.close()
            mgr.close()

        db.close.assert_called_once()

    def test_close_tolerates_failing_close(self):
        mgr = SessionManager()
        db = MagicMock()
        db.close.side_effect = RuntimeError("already closed")
        with patch("hermes_state.SessionDB", return_value=db):
            mgr._get_db()
            mgr.close()  # must not raise

    def test_close_without_db_is_a_noop(self):
        SessionManager().close()  # never opened; must not raise

    def test_get_db_reopens_after_close(self):
        mgr = SessionManager()
        first = MagicMock()
        with patch("hermes_state.SessionDB", return_value=first):
            mgr._get_db()
            mgr.close()

        second = MagicMock()
        with patch("hermes_state.SessionDB", return_value=second):
            assert mgr._get_db() is second
        first.close.assert_called_once()
        second.close.assert_not_called()

    def test_close_does_not_close_injected_db(self):
        """A caller-injected db (``SessionManager(db=...)``) stays the caller's
        responsibility — close() must not close a handle it does not own."""
        injected = MagicMock()
        mgr = SessionManager(db=injected)
        mgr._get_db()
        mgr.close()

        injected.close.assert_not_called()


class TestAcpEntryTeardownClosesSessionManager:
    """The SessionManager.close() contract is only real if the ACP entry
    point actually calls it on teardown — otherwise the writable handle is
    still left to GC/process exit (P0-C §2e #21, wiring requirement)."""

    def test_main_closes_session_manager_on_normal_exit(self, monkeypatch):
        """``acp.run_agent`` returning (stdin EOF / client disconnect) must
        close the SessionManager in the ``finally`` block."""
        from unittest.mock import patch

        from acp_adapter import entry

        async def _fake_run_agent(*args, **kwargs):
            return None

        monkeypatch.setenv("HERMES_ACP_SKIP_CONFIGURED_MCP", "1")
        agent = MagicMock()
        with patch.object(entry, "_setup_logging"), \
             patch.object(entry, "_load_env"), \
             patch("acp.run_agent", new=_fake_run_agent), \
             patch("acp_adapter.server.HermesACPAgent", return_value=agent):
            entry.main([])

        agent.session_manager.close.assert_called_once()

    def test_main_closes_session_manager_on_crash(self, monkeypatch):
        """A crashing agent must still close the SessionManager — the finally
        block runs before ``sys.exit(1)`` propagates."""
        from unittest.mock import patch

        import pytest

        from acp_adapter import entry

        async def _fake_run_agent(*args, **kwargs):
            raise RuntimeError("acp crashed")

        monkeypatch.setenv("HERMES_ACP_SKIP_CONFIGURED_MCP", "1")
        agent = MagicMock()
        with patch.object(entry, "_setup_logging"), \
             patch.object(entry, "_load_env"), \
             patch("acp.run_agent", new=_fake_run_agent), \
             patch("acp_adapter.server.HermesACPAgent", return_value=agent), \
             pytest.raises(SystemExit):
            entry.main([])

        agent.session_manager.close.assert_called_once()
