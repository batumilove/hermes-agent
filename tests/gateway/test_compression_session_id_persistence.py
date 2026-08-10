"""Behavioral coverage for compression-driven gateway route rebinding."""

from gateway.session import SessionSource
from gateway.config import Platform

from tests.gateway.test_compression_failure_session_sync import (
    SESSION_KEY,
    _SessionStore,
    _install_compression_failure_agent,
    _run_compression_failure_turn,
    _runner,
)


class _RejectingSessionStore(_SessionStore):
    def __init__(self):
        super().__init__()
        self.rebind_calls = []

    def rebind_session_id(self, session_key, expected_session_id, new_session_id):
        self.rebind_calls.append((session_key, expected_session_id, new_session_id))
        return False


def test_agent_compression_does_not_sync_after_route_rebind_rejection(
    monkeypatch, tmp_path
):
    """A failed route CAS must not publish child peer or topic bindings."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _install_compression_failure_agent(monkeypatch)
    session_store = _RejectingSessionStore()
    runner = _runner(session_store)
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="user-1",
    )

    result = _run_compression_failure_turn(runner, source)

    assert result["failed"] is True
    assert result["session_id"] == "session-after-compression"
    assert session_store.entry.session_id == "session-before-compression"
    assert session_store.rebind_calls
    assert set(session_store.rebind_calls) == {
        (
            SESSION_KEY,
            "session-before-compression",
            "session-after-compression",
        )
    }
    assert session_store.peer_records == []
    runner._sync_telegram_topic_binding.assert_not_called()
