"""Regression: first-turn ``session_meta`` row must be re-baselined into the
agent cache's message_count snapshot.

Bug
---
On a fresh gateway conversation the post-turn re-baseline
(``_refresh_agent_cache_message_count``) runs *before* the first-turn
``session_meta`` marker row is appended to the transcript:

    gateway/run.py:
        ... agent run completes ...
        self._refresh_agent_cache_message_count(...)   # snapshot taken HERE
        ...
        if not history:                                # session_meta written LATER
            append_to_transcript({"role": "session_meta", ...})

``append_to_transcript`` (no ``skip_db``) increments the session's
``message_count`` unconditionally (hermes_state.append_message), so the
snapshot ends up exactly +1 below the live on-disk count.

The cross-process coherence guard (#45966) compares the live count against
that snapshot on the *next* inbound message, sees ``live != snapshot``,
mistakes this process's own ``session_meta`` write for a foreign write, and
**rebuilds the cached agent on turn 2 of every fresh conversation** — silently
busting the per-conversation prompt cache the cache exists to protect.

The fix re-baselines AFTER all of this turn's transcript writes (including the
first-turn ``session_meta`` row), so the snapshot matches the live count and
the guard fires only on genuinely foreign writes.

This drives the REAL ``_handle_message_with_agent`` against a REAL SessionDB
(the ``session_meta`` write actually increments ``message_count``) and asserts
the invariant: after a first turn, snapshot == live count → next turn reuses.
"""

import sys
import threading
import types
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource


SESSION_KEY = "agent:main:telegram:group:-1001:12345"
SESSION_ID = "sess-first-turn"


def _bootstrap(monkeypatch, tmp_path, db):
    """GatewayRunner wired to a REAL SessionDB for count reads, mirroring the
    proven #42039 harness but with a live cache + real transcript counter."""
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    config = GatewayConfig()
    runner = gateway_run.GatewayRunner(config)
    runner.adapters = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._handle_active_session_busy_message = AsyncMock(return_value=False)
    # REAL SessionDB behind the async facade the gateway holds — the
    # production re-baseline does ``await self._session_db.get_session(...)``,
    # so it must be the AsyncSessionDB wrapper, not the raw sync DB.
    from hermes_state import AsyncSessionDB

    runner._session_db = AsyncSessionDB(db)
    runner._recover_telegram_topic_thread_id = lambda _source: None
    runner._cache_session_source = lambda _key, _source: None
    runner._is_session_run_current = lambda _key, _gen: True
    runner._begin_session_run_generation = lambda _key: 1
    runner._reply_anchor_for_event = lambda _event: None
    runner._get_guild_id = lambda _event: None
    runner._should_send_voice_reply = lambda *_a, **_kw: False
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()

    # Live agent cache (not a MagicMock) so the re-baseline actually rewrites
    # the snapshot tuple in place.
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()

    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key=SESSION_KEY,
        session_id=SESSION_ID,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )
    # Empty history → triggers the first-turn ``session_meta`` write path.
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_platform_message_id.return_value = False
    runner.session_store.update_session = MagicMock()

    # Mirror the real SessionStore.append_to_transcript: forward non-skip_db
    # writes to the real DB so a ``session_meta`` row genuinely increments
    # message_count, exactly as in production. skip_db=True writes (the agent
    # already persisted them via _flush_messages_to_session_db) are no-ops here.
    def _append(session_id, message, skip_db=False):
        if not skip_db:
            db.append_message(
                session_id=session_id,
                role=message.get("role", "unknown"),
                content=message.get("content"),
                timestamp=message.get("timestamp"),
            )

    runner.session_store.append_to_transcript = MagicMock(side_effect=_append)
    runner.session_store.rebind_session_id = MagicMock(return_value=True)
    runner.session_store.clear_turn_active = MagicMock(return_value=True)
    runner.session_store.clear_resume_pending = MagicMock(return_value=True)
    runner.session_store.mark_turn_active = MagicMock(return_value="first-turn-token")
    runner.session_store.load_durable_routing_entry.return_value = {
        "last_prompt_tokens": 0,
        "resume_pending": False,
    }
    runner.session_store.lookup_by_session_key.return_value = (
        runner.session_store.get_or_create_session.return_value
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"}
    )
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100_000,
    )
    return runner


def _event():
    return MessageEvent(
        text="hello world",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1001",
            chat_type="group",
            user_id="12345",
        ),
        message_id="msg-1",
    )


def _source():
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="-1001",
        chat_type="group",
        user_id="12345",
    )


def _live_count(db, session_id):
    row = db.get_session(session_id)
    return (row.get("message_count", 0) if row else 0)


@pytest.mark.asyncio
async def test_first_turn_session_meta_is_captured_by_rebaseline(
    monkeypatch, tmp_path
):
    """After a fresh first turn, the cache snapshot must equal the live
    message_count — including the first-turn ``session_meta`` row.

    WITHOUT the fix the re-baseline snapshots the count *before* the
    session_meta append, leaving the snapshot one short; the cross-process
    guard then rebuilds the cached agent on turn 2 (prompt-cache churn).
    """
    from hermes_state import SessionDB

    db = SessionDB(db_path=tmp_path / "sessions.db")
    db.create_session(SESSION_ID, source="telegram")

    runner = _bootstrap(monkeypatch, tmp_path, db)
    # The production handoff captures its owning DB while the turn is still
    # active. Install this harness's real DB before creating that handoff.
    runner.session_store._db = db

    # Cache snapshot taken at agent-BUILD time = count before this turn's
    # writes (a fresh session → 0). This is what the #45966 guard stores.
    build_count = _live_count(db, SESSION_ID)
    agent_obj = object()
    with runner._agent_cache_lock:
        runner._agent_cache[SESSION_KEY] = (agent_obj, "sig", build_count)

    # Stubbed agent run: the gateway's own user/assistant rows are persisted
    # by the agent (skip_db=True downstream); only the session_meta marker is
    # written by the gateway with skip_db=False.
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "Hi there!",
            "messages": [
                {"role": "user", "content": "hello world"},
                {"role": "assistant", "content": "Hi there!"},
            ],
            "tools": [{"name": "noop"}],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    event = _event()
    event._gateway_active_turn_session_key = SESSION_KEY
    event._gateway_active_turn_token = "first-turn-token"
    handoff = await runner._handle_message_with_agent(event, _source(), SESSION_KEY, 1)

    # Drive the exact durable path that Base uses after its send settles. The
    # production worker is runner-owned, so replace the constructor-created
    # store worker after this lightweight harness installs its SessionDB.
    runner._post_response_controller.shutdown(1.0)
    runner.session_store._db = db
    from gateway.post_response import PostResponseWorker

    runner._post_response_controller = PostResponseWorker(runner)
    runner._post_response_worker = runner._post_response_controller.start()
    import json
    json.dumps(handoff.actions_for_text("Hi there!"), sort_keys=True)
    assert runner._prepare_final_response_handoff(
        handoff, text_content="Hi there!", event=_event()
    )
    assert db.mark_final_delivery_attempting(handoff.obligation_id)
    assert db.settle_final_delivery(handoff.obligation_id, delivered=True)
    runner._post_response_controller.wake()
    deadline = __import__("time").monotonic() + 2.0
    while db.has_post_response_barrier(SESSION_KEY, SESSION_ID) and __import__("time").monotonic() < deadline:
        __import__("time").sleep(0.01)
    assert not db.has_post_response_barrier(SESSION_KEY, SESSION_ID)

    # The first-turn session_meta row was written → live count advanced.
    live = _live_count(db, SESSION_ID)
    assert live == build_count + 1, (
        "first-turn session_meta should increment message_count by exactly 1"
    )

    # THE INVARIANT: the cache snapshot must now equal the live count, so the
    # next turn's cross-process guard reuses the cached agent.
    with runner._agent_cache_lock:
        cached = runner._agent_cache[SESSION_KEY]
    snapshot = cached[2]
    assert snapshot == live, (
        f"cache snapshot {snapshot} != live count {live}: the first-turn "
        f"session_meta write was not re-baselined, so the #45966 guard will "
        f"rebuild the cached agent on turn 2 and bust the prompt cache."
    )
    # And the cached agent instance must be untouched (never rebuilt).
    assert cached[0] is agent_obj
    assert runner.shutdown_post_response_worker(timeout=1.0)
