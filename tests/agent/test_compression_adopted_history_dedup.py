from tests.agent import compression_fixtures
_fixed_context_window = compression_fixtures._fixed_context_window
"""Retained behavioral fork regression."""
from pathlib import Path
from hermes_state import SessionDB
from tests.agent.test_compression_concurrent_fork import _build_agent_with_db

def test_aborted_compression_does_not_reappend_adopted_durable_history(
    tmp_path: Path,
) -> None:
    """An adopted durable snapshot remains persistence-idempotent on abort.

    Rotation-mode compression reloads a longer durable transcript after taking
    the lease. If summary generation then aborts, the returned dictionaries are
    fresh DB projections rather than objects from ``conversation_history``.
    A later error-path persist must recognize every adopted row as already
    durable instead of appending the whole transcript again.
    """
    db = SessionDB(db_path=tmp_path / "state.db")
    session_id = "PRE_LEASE_ABORT_DEDUP"
    db.create_session(session_id, source="telegram")
    db.append_message(session_id, "user", "old durable")

    stale_snapshot = [{"role": "user", "content": "old durable"}]
    db.append_message(session_id, "assistant", "late committed before lease")
    agent = _build_agent_with_db(db, session_id)

    def _abort_summary(messages, **_kwargs):
        agent.context_compressor._last_compress_aborted = True
        agent.context_compressor._last_summary_error = "auxiliary provider unavailable"
        return messages

    agent.context_compressor.compress.side_effect = _abort_summary
    returned, _system_prompt = agent._compress_context(
        stale_snapshot, "sys", approx_tokens=120_000
    )

    assert [message["content"] for message in returned] == [
        "old durable",
        "late committed before lease",
    ]
    assert agent.session_id == session_id

    # Mirrors conversation_loop's error exit after an aborted compression.
    agent._persist_session(returned, stale_snapshot)

    durable = db.get_messages_as_conversation(session_id)
    assert [message["content"] for message in durable] == [
        "old durable",
        "late committed before lease",
    ]
