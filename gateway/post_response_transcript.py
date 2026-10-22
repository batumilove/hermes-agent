"""Physical transcript row verification for idempotent durable action replay."""
import json
from hermes_state_messages import _scrub_surrogates

def terminal_row_matches(store, session_id, message) -> bool:
    # Verify the physical SessionDB row, not SessionStore's live
    # replay projection: that projection repairs alternation and
    # can omit session_meta fields, while a queued/spooled write
    # must never count as completion.
    rows = store._db.get_messages(
        session_id, latest=True, limit=1
    )
    if not rows:
        return False
    row = rows[-1]
    db = store._db
    role = message.get("role", "unknown")
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, str):
        try:
            tool_calls = json.loads(tool_calls)
        except (json.JSONDecodeError, TypeError):
            tool_calls = []

    # Match SessionStore._append_transcript_message's complete
    # SessionDB payload, not only the replay-visible role/content
    # subset.  These canonicalizations are precisely the ones
    # SessionDB applies while serializing the row.
    expected = {
        "role": role,
        "content": db._decode_content(db._encode_content(message.get("content"))),
        "tool_call_id": message.get("tool_call_id"),
        "tool_calls": tool_calls if tool_calls else None,
        "tool_name": _scrub_surrogates(message.get("tool_name")),
        "effect_disposition": None,
        "token_count": None,
        "finish_reason": None,
        "reasoning": (
            _scrub_surrogates(message.get("reasoning"))
            if role == "assistant" else None
        ),
        "reasoning_content": (
            _scrub_surrogates(message.get("reasoning_content"))
            if role == "assistant" else None
        ),
        "reasoning_details": db._reasoning_json_text(
            message.get("reasoning_details") if role == "assistant" else None
        ),
        "codex_reasoning_items": db._reasoning_json_text(
            message.get("codex_reasoning_items") if role == "assistant" else None
        ),
        "codex_message_items": db._reasoning_json_text(
            message.get("codex_message_items") if role == "assistant" else None
        ),
        "platform_message_id": (
            message.get("platform_message_id") or message.get("message_id")
        ),
        "observed": 1 if message.get("observed") else 0,
        "api_content": _scrub_surrogates(
            message.get("api_content")
            if isinstance(message.get("api_content"), str) else None
        ),
        "display_kind": _scrub_surrogates(message.get("display_kind")),
        "display_metadata": db._decode_display_metadata(
            db._encode_display_metadata(message.get("display_metadata"))
        ),
    }
    timestamp = message.get("timestamp")
    if timestamp is not None:
        try:
            expected["timestamp"] = float(
                timestamp.timestamp() if hasattr(timestamp, "timestamp") else timestamp
            )
        except (TypeError, ValueError):
            # SessionDB substitutes time.time() for an absent or
            # malformed timestamp; no stable intended value exists
            # to verify in that case.
            pass
    for field, value in expected.items():
        if row.get(field) != value:
            return False
    return True
