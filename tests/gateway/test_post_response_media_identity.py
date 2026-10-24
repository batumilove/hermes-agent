"""Durable media-only receipt identity and replay idempotence."""
import pytest
from tests.gateway.test_post_response_delivery import (
    AsyncMock,
    CaptureFinalComponentsAdapter,
    CaptureSlackAdapter,
    FinalResponseHandoff,
    _close_runner,
    _db_counts,
    _deliver,
    _entry,
    _event,
    _only_obligation_row,
    _runner,
    _source,
    _wait_until,
    build_session_key,
    phase2_env as _shared_phase2_env,
)

@pytest.fixture(name="phase2_env")
def media_receipt_home(tmp_path, monkeypatch):
    return _shared_phase2_env.__wrapped__(tmp_path, monkeypatch)

@pytest.mark.asyncio
async def test_media_only_missing_message_ids_get_distinct_safe_handoff_obligations(phase2_env):
    """Recovery text is safe and constant, but source handoffs remain distinct."""
    runner = None
    source = _source("C-media-identity", "T-media-identity")
    first_path = "/private/secret/first.png"
    second_path = "/private/secret/second.png"
    adapter = CaptureFinalComponentsAdapter()
    adapter.extract_media = lambda response: ([(response.removeprefix("MEDIA:"), False)], "")
    adapter.filter_media_delivery_paths = lambda media, **_kwargs: media
    try:
        runner = _runner(phase2_env, adapter)
        runner._run_agent = AsyncMock(side_effect=[
            {"final_response": "MEDIA:" + first_path, "messages": [], "api_calls": 1},
            {"final_response": "MEDIA:" + second_path, "messages": [], "api_calls": 1},
        ])
        await _deliver(adapter, source, message_id="", text="first")
        assert _wait_until(lambda: not runner.session_store._db.has_post_response_barrier(
            build_session_key(source), _entry(runner, source).session_id
        ))
        await _deliver(adapter, source, message_id="", text="second")
        with runner.session_store._db._read_ctx() as conn:
            rows = conn.execute(
                "SELECT obligation_id, content FROM delivery_obligations ORDER BY obligation_id"
            ).fetchall()
        assert len(rows) == 2
        assert len({row["obligation_id"] for row in rows}) == 2
        assert adapter.outputs == ["image", "image"]
        assert all(first_path not in row["content"] and second_path not in row["content"] for row in rows)
    finally:
        _close_runner(runner)


def test_same_missing_id_media_handoff_replay_is_idempotent(phase2_env):
    """The same original media handoff maps back to its one safe receipt."""
    runner = None
    source = _source("C-media-replay", "T-media-replay")
    raw_path = "/private/secret/replay.png"
    try:
        runner = _runner(phase2_env, CaptureSlackAdapter())
        entry = _entry(runner, source)
        handoff = FinalResponseHandoff(
            "MEDIA:" + raw_path,
            runner=runner,
            session_key=entry.session_key,
            session_lineage=entry.session_id,
            expected_session_id=entry.session_id,
            new_session_id=None,
            active_turn_token="stable-media-token",
            last_prompt_tokens=0,
            touch_activity=True,
            append_final_text=False,
            clear_resume_pending=False,
            source_data=source.to_dict(),
        )
        event = _event(source, message_id="")
        recovery = "⚠️ The gateway was interrupted while delivering an attachment response. Please ask me to resend it."
        assert runner._prepare_final_response_handoff(handoff, text_content=recovery, event=event)
        first_id = handoff.obligation_id
        assert runner._prepare_final_response_handoff(handoff, text_content=recovery, event=event)
        assert handoff.obligation_id == first_id
        assert _db_counts(runner.session_store._db)[0] == 1
        assert raw_path not in _only_obligation_row(runner.session_store._db)[1]
    finally:
        _close_runner(runner)
