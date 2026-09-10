"""Integration contract for durable post-response processing.

These tests exercise the real GatewayRunner -> BasePlatformAdapter delivery
path and real SessionStore/SessionDB state. Only the model boundary, platform
edge, and deliberately named fault-injection points are substituted.
"""

import asyncio
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway import delivery_ledger
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.run import GatewayRunner
from gateway.session import SessionSource, SessionStore, build_session_key
from gateway.post_response import FinalResponseHandoff, PostResponseWorker


class CaptureSlackAdapter(BasePlatformAdapter):
    """Real base-adapter processing with a deterministic Slack network edge."""

    def __init__(self, before_send=None):
        super().__init__(PlatformConfig(enabled=True, token="test-token"), Platform.SLACK)
        self.before_send = before_send
        self.sent = []
        self.send_started = threading.Event()

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.send_started.set()
        if self.before_send is not None:
            self.before_send(chat_id, content, reply_to, metadata)
        self.sent.append((chat_id, content, reply_to, metadata))
        return SendResult(success=True, message_id=f"slack-{len(self.sent)}")

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        return None

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


@pytest.fixture
def phase2_env(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("SLACK_HOME_CHANNEL", "C-phase2")
    return home


def _source(chat_id="C-phase2", thread_id="T-phase2"):
    return SessionSource(
        platform=Platform.SLACK,
        chat_id=chat_id,
        chat_type="channel",
        thread_id=thread_id,
        user_id="U-phase2",
    )


def _event(source, message_id="phase2-message", text="hello"):
    return MessageEvent(text=text, source=source, message_id=message_id)


def _runner(home: Path, adapter: CaptureSlackAdapter, result=None):
    config = GatewayConfig(
        platforms={Platform.SLACK: PlatformConfig(enabled=True, token="test-token")},
        sessions_dir=home / "sessions",
    )
    runner = GatewayRunner(config)
    runner.adapters = {Platform.SLACK: adapter}
    runner._is_user_authorized = lambda _source: True
    runner._run_agent = AsyncMock(
        return_value=result
        or {
            "final_response": "phase-2 final",
            "messages": [],
            "api_calls": 1,
            "last_prompt_tokens": 0,
        }
    )
    adapter.set_message_handler(runner._handle_message)
    adapter._keep_typing = lambda *_args, **_kwargs: asyncio.Event().wait()
    return runner


async def _deliver(adapter, source, *, message_id="phase2-message", text="hello"):
    event = _event(source, message_id=message_id, text=text)
    await adapter._process_message_background(event, build_session_key(source))
    return event


def _entry(runner, source):
    return runner.session_store.get_or_create_session(source)


def _db_counts(db):
    with db._read_ctx() as conn:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("delivery_obligations", "gateway_post_response_actions")
        )


def _seed_barrier(db, entry, *, obligation_id="phase2-obligation", actions=None):
    actions = actions or [
        {
            "action_kind": "rebind_session_id",
            "payload": {
                "session_key": entry.session_key,
                "expected_session_id": entry.session_id,
                "new_session_id": "phase2-child",
            },
        },
        {
            "action_kind": "append_to_transcript",
            "payload": {
                "session_id": "phase2-child",
                "message": {"role": "assistant", "content": "phase-2 final"},
            },
        },
        {
            "action_kind": "update_session",
            "payload": {
                "session_key": entry.session_key,
                "last_prompt_tokens": 7,
            },
        },
    ]
    db.create_final_delivery_and_barrier(
        obligation_id=obligation_id,
        session_key=entry.session_key,
        session_lineage=entry.session_id,
        platform="slack",
        chat_id="C-phase2",
        thread_id="T-phase2",
        content="phase-2 final",
        actions=actions,
    )
    return obligation_id


def _wait_until(predicate, *, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _require_post_response_worker(runner):
    """Give an actionable RED failure until the runner owns this lifecycle."""
    shutdown = getattr(runner, "shutdown_post_response_worker", None)
    assert callable(shutdown), (
        "Phase-2 contract missing: GatewayRunner must expose "
        "shutdown_post_response_worker(timeout=...) for its owned durable "
        "post-response worker."
    )
    worker = getattr(runner, "_post_response_worker", None)
    assert isinstance(worker, threading.Thread), (
        "Phase-2 contract missing: GatewayRunner must retain its one "
        "long-lived worker as _post_response_worker so shutdown ownership is "
        "observable and testable."
    )
    assert worker.is_alive(), "Phase-2 post-response worker was not started by the runner."
    return worker, shutdown


def _close_runner(runner):
    """Never leave a future worker or its SQLite handles behind after RED runs."""
    if runner is None:
        return
    shutdown = getattr(runner, "shutdown_post_response_worker", None)
    if callable(shutdown):
        try:
            shutdown(timeout=2.0)
        except Exception:
            pass
    runner.session_store.close_all_db_handles()


def _obligation_state(db, obligation_id):
    with db._read_ctx() as conn:
        row = conn.execute(
            "SELECT state FROM delivery_obligations WHERE obligation_id=?", (obligation_id,)
        ).fetchone()
    return None if row is None else row[0]


class CaptureFinalComponentsAdapter(CaptureSlackAdapter):
    """Capture every final-output surface and inspect its durable fence."""

    def __init__(self, before_output=None):
        super().__init__()
        self.before_output = before_output
        self.outputs = []

    def _before_output(self, kind):
        if self.before_output is not None:
            self.before_output(kind)

    async def send(self, *args, **kwargs):
        self._before_output("text")
        self.outputs.append("text")
        return await super().send(*args, **kwargs)

    async def play_tts(self, chat_id, audio_path, **kwargs):
        self._before_output("tts")
        self.outputs.append("tts")
        return SendResult(success=True, message_id="tts-1")

    async def send_multiple_images(self, chat_id, images, metadata=None, human_delay=0.0):
        self._before_output("image")
        self.outputs.append("image")
        return SendResult(success=True, message_id="image-1")

    async def send_document(self, chat_id, file_path, **kwargs):
        self._before_output("document")
        self.outputs.append("document")
        return SendResult(success=True, message_id="document-1")


class LegacyNoneImageBatchAdapter(CaptureFinalComponentsAdapter):
    """Compatibility shape used by older built-in plugin overrides."""

    async def send_multiple_images(self, chat_id, images, metadata=None, human_delay=0.0):
        self._before_output("image")
        self.outputs.append("image")
        # Historically these overrides completed the platform side effect and
        # returned no result object.
        return None


class ComponentOutcomeAdapter(CaptureFinalComponentsAdapter):
    """Final-component adapter with independently controlled outcomes."""

    def __init__(self, *, text=True, image=True, tts=True, document=True, before_output=None):
        super().__init__(before_output)
        self._outcomes = {
            "text": text,
            "image": image,
            "tts": tts,
            "document": document,
        }

    async def send(self, *args, **kwargs):
        self._before_output("text")
        self.outputs.append("text")
        if self._outcomes["text"]:
            return await CaptureSlackAdapter.send(self, *args, **kwargs)
        return SendResult(success=False, error="text rejected")

    async def play_tts(self, *args, **kwargs):
        self._before_output("tts")
        self.outputs.append("tts")
        return SendResult(success=self._outcomes["tts"], error="tts rejected")

    async def send_multiple_images(self, *args, **kwargs):
        self._before_output("image")
        self.outputs.append("image")
        return SendResult(success=self._outcomes["image"], error="image rejected")

    async def send_document(self, *args, **kwargs):
        self._before_output("document")
        self.outputs.append("document")
        return SendResult(success=self._outcomes["document"], error="document rejected")


def _only_obligation_row(db):
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT obligation_id, content, state FROM delivery_obligations"
        ).fetchall()
    assert len(rows) == 1
    return rows[0]


@pytest.mark.asyncio
async def test_final_delivery_is_precommitted_while_runner_worker_is_blocked_in_real_rebind(
    phase2_env, monkeypatch
):
    """Base sends while the worker is actively blocked in SessionStore.rebind_session_id."""
    entered_rebind = threading.Event()
    release_rebind = threading.Event()
    observed = {}
    runner = None
    worker = None
    original_rebind = SessionStore.rebind_session_id

    def blocking_rebind(store, *args, **kwargs):
        assert store is runner.session_store, "worker must execute the runner store's real rebind"
        assert threading.current_thread() is worker, (
            "post-response rebind ran outside the one runner-owned worker"
        )
        entered_rebind.set()
        assert release_rebind.wait(2.0), "test did not release the blocked worker"
        return original_rebind(store, *args, **kwargs)

    def before_send(*_args):
        assert entered_rebind.wait(1.0), (
            "BasePlatformAdapter sent before the runner-owned worker entered "
            "the durable rebind action"
        )
        db = runner.session_store._db
        entry = _entry(runner, source)
        observed["counts_at_send"] = _db_counts(db)
        observed["barrier_at_send"] = db.has_post_response_barrier(
            entry.session_key, entry.session_id
        )
        observed["rebind_still_blocked"] = not release_rebind.is_set()

    adapter = CaptureSlackAdapter(before_send=before_send)
    source = _source()
    try:
        runner = _runner(
            phase2_env,
            adapter,
            result={
                "final_response": "phase-2 final",
                "messages": [],
                "api_calls": 1,
                "last_prompt_tokens": 7,
                "session_id": "phase2-child",
            },
        )
        worker, _shutdown = _require_post_response_worker(runner)
        monkeypatch.setattr(SessionStore, "rebind_session_id", blocking_rebind)

        await _deliver(adapter, source)

        # Gateway-owned first-turn session_meta + fallback user/assistant
        # rows, metadata/restart cleanup, and terminal finalization are all
        # durable actions; none await on the adapter path.
        assert observed["counts_at_send"] == (1, 5)
        assert observed["barrier_at_send"] is True
        assert observed["rebind_still_blocked"] is True
        assert adapter.sent[0][1] == "phase-2 final"

        release_rebind.set()
        entry = _entry(runner, source)
        assert _wait_until(
            lambda: not runner.session_store._db.has_post_response_barrier(
                entry.session_key, entry.session_id
            )
        ), "worker did not drain the rebind/transcript/update barrier after release"
        _worker, shutdown = _require_post_response_worker(runner)
        assert shutdown(timeout=1.0) is True, "worker did not cleanly drain on shutdown"
    finally:
        release_rebind.set()
        _close_runner(runner)


@pytest.mark.asyncio
async def test_receipt_commit_failure_never_sends_or_clears_the_real_active_turn(
    phase2_env, monkeypatch
):
    """A transaction failure keeps the pre-existing active token and sends nothing."""
    adapter = CaptureSlackAdapter()
    runner = None
    source = _source()
    observed = {}
    try:
        runner = _runner(phase2_env, adapter)
        _require_post_response_worker(runner)

        def fail_receipt(*_args, **_kwargs):
            active_entry = _entry(runner, source)
            assert active_entry.active_turn_token, (
                "receipt transaction was attempted before the real durable active-turn "
                "token existed"
            )
            observed["active_turn_token"] = active_entry.active_turn_token
            raise OSError("injected atomic receipt failure")

        monkeypatch.setattr(
            runner.session_store._db, "create_final_delivery_and_barrier", fail_receipt
        )
        await _deliver(adapter, source)

        active_entry = _entry(runner, source)
        assert observed["active_turn_token"] == active_entry.active_turn_token
        assert adapter.sent == []
        assert _db_counts(runner.session_store._db) == (0, 0)
    finally:
        _close_runner(runner)


def test_startup_worker_replays_delivered_receipt_without_rerunning_model_and_releases_barrier(
    phase2_env,
):
    """A delivered receipt survives a crash before the rebind/transcript tail."""
    source = _source()
    config = GatewayConfig(
        platforms={Platform.SLACK: PlatformConfig(enabled=True, token="test-token")},
        sessions_dir=phase2_env / "sessions",
    )
    store = SessionStore(config.sessions_dir, config)
    restarted = None
    try:
        entry = store.get_or_create_session(source)
        store._db.create_session("phase2-child", "gateway")
        assert store.rewrite_transcript(
            "phase2-child", [{"role": "user", "content": "hello"}]
        )
        token = store.mark_turn_active(entry.session_key)
        assert token
        obligation_id = _seed_barrier(
            store._db,
            entry,
            actions=[
                {
                    "action_kind": "rebind_session_id",
                    "payload": {
                        "session_key": entry.session_key,
                        "expected_session_id": entry.session_id,
                        "new_session_id": "phase2-child",
                    },
                },
                {
                    "action_kind": "append_to_transcript",
                    "payload": {
                        "session_id": "phase2-child",
                        "message": {"role": "assistant", "content": "phase-2 final"},
                    },
                },
                {
                    "action_kind": "finalize_turn",
                    "payload": {
                        "session_key": entry.session_key,
                        "active_turn_token": token,
                        "session_id": "phase2-child",
                    },
                },
            ],
        )
        store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
                (obligation_id,),
            )
        )
    finally:
        # Old process: close it before a fresh runner opens the same state DB.
        store.close_all_db_handles()

    try:
        restarted = _runner(phase2_env, CaptureSlackAdapter())
        _require_post_response_worker(restarted)
        restarted._run_agent.reset_mock()

        assert _wait_until(
            lambda: not restarted.session_store._db.has_post_response_barrier(
                entry.session_key, entry.session_id
            )
        ), "startup worker did not replay the delivered receipt"
        restored = restarted.session_store.get_or_create_session(source)
        transcript = restarted.session_store.load_transcript("phase2-child")
        assert restored.session_id == "phase2-child"
        assert [message["role"] for message in transcript] == ["user", "assistant"]
        assert restarted._run_agent.await_count == 0
        assert restored.active_turn_token is None
    finally:
        _close_runner(restarted)


def test_startup_pending_receipt_keeps_active_token_and_barrier_until_redelivery_settles(
    phase2_env,
):
    """A fresh runner must not finalize before the crash-left send is settled."""
    source = _source("C-pending", "T-pending")
    config = GatewayConfig(
        platforms={Platform.SLACK: PlatformConfig(enabled=True, token="test-token")},
        sessions_dir=phase2_env / "sessions",
    )
    store = SessionStore(config.sessions_dir, config)
    restarted = None
    try:
        entry = store.get_or_create_session(source)
        token = store.mark_turn_active(entry.session_key)
        assert token
        obligation_id = _seed_barrier(
            store._db,
            entry,
            obligation_id="startup-pending",
            actions=[
                {
                    "action_kind": "update_session",
                    "payload": {"session_key": entry.session_key},
                },
                {
                    "action_kind": "finalize_turn",
                    "payload": {
                        "session_key": entry.session_key,
                        "active_turn_token": token,
                        "session_id": entry.session_id,
                    },
                },
            ],
        )
    finally:
        store.close_all_db_handles()

    try:
        restarted = _runner(phase2_env, CaptureSlackAdapter())
        _require_post_response_worker(restarted)
        restored = restarted.session_store.get_or_create_session(source)
        assert _wait_until(
            lambda: (
                _obligation_state(restarted.session_store._db, obligation_id) == "pending"
                and (
                    diagnostics := restarted.session_store._db.get_post_response_barrier_diagnostics(
                        obligation_id
                    )
                )
                and diagnostics[-1]["action_kind"] == "finalize_turn"
                and diagnostics[-1]["state"] == "pending"
                and all(action["state"] == "done" for action in diagnostics[:-1])
            ),
            timeout=PostResponseWorker._ACTION_POLL_SECONDS * 4,
        ), "startup worker finalized before the pending receipt was settled"
        assert restored.active_turn_token == token
        assert restarted.session_store._db.has_post_response_barrier(
            entry.session_key, entry.session_id
        )
        assert restarted._run_agent.await_count == 0

        assert restarted.session_store._db.mark_final_delivery_attempting(obligation_id)
        assert restarted.session_store._db.settle_final_delivery(obligation_id, delivered=True)
        restarted._post_response_controller.wake()
        assert _wait_until(
            lambda: not restarted.session_store._db.has_post_response_barrier(
                entry.session_key, entry.session_id
            )
        )
        assert restarted.session_store.get_or_create_session(source).active_turn_token is None
    finally:
        _close_runner(restarted)


@pytest.mark.asyncio
async def test_restart_redelivers_failed_receipt_then_wakes_finalizer_without_rerunning_model(
    phase2_env, monkeypatch,
):
    """Recovery redelivery, not a second model turn, releases a failed handoff."""
    source = _source("C-failed-restart", "T-failed-restart")
    config = GatewayConfig(
        platforms={Platform.SLACK: PlatformConfig(enabled=True, token="test-token")},
        sessions_dir=phase2_env / "sessions",
    )
    store = SessionStore(config.sessions_dir, config)
    restarted = None
    try:
        entry = store.get_or_create_session(source)
        token = store.mark_turn_active(entry.session_key)
        assert token
        obligation_id = _seed_barrier(
            store._db,
            entry,
            obligation_id="startup-failed",
            actions=[
                {
                    "action_kind": "update_session",
                    "payload": {"session_key": entry.session_key},
                },
                {
                    "action_kind": "finalize_turn",
                    "payload": {
                        "session_key": entry.session_key,
                        "active_turn_token": token,
                        "session_id": entry.session_id,
                    },
                },
            ],
        )
        assert store._db.mark_final_delivery_attempting(obligation_id)
        assert store._db.settle_final_delivery(
            obligation_id, delivered=False, error="injected rejection",
        )
        # A restart only recovers receipts whose former owner is dead.
        store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET owner_pid=999999999, "
                "owner_started_at=1 WHERE obligation_id=?",
                (obligation_id,),
            )
        )
    finally:
        store.close_all_db_handles()

    try:
        states_at_redelivery_send = []

        def before_redelivery_send(*_args):
            states_at_redelivery_send.append(
                _obligation_state(restarted.session_store._db, obligation_id)
            )

        adapter = CaptureSlackAdapter(before_send=before_redelivery_send)
        restarted = _runner(phase2_env, adapter)
        _require_post_response_worker(restarted)
        restarted._run_agent.reset_mock()
        monkeypatch.setattr(delivery_ledger, "ledger_enabled", lambda: True)
        # The suite redirects SessionStore into its isolated test profile;
        # exercise recovery against the exact receipt DB the worker owns.
        monkeypatch.setattr(
            delivery_ledger, "_db_path", lambda: restarted.session_store._db.db_path
        )
        wake_called = threading.Event()
        original_wake = restarted._post_response_controller.wake

        def capture_wake():
            wake_called.set()
            original_wake()

        monkeypatch.setattr(restarted._post_response_controller, "wake", capture_wake)
        assert _wait_until(
            lambda: (
                _obligation_state(restarted.session_store._db, obligation_id) == "failed"
                and restarted.session_store._db.has_post_response_barrier(
                    entry.session_key, entry.session_id
                )
                and restarted.session_store.get_or_create_session(source).active_turn_token == token
            ),
            timeout=PostResponseWorker._ACTION_POLL_SECONDS * 4,
        ), "failed receipt did not retain its restart recovery barrier"

        assert await restarted._redeliver_pending_obligations() == 1
        assert wake_called.is_set(), "redelivery did not wake the finalization worker"
        assert _obligation_state(restarted.session_store._db, obligation_id) == "delivered"
        assert states_at_redelivery_send == ["attempting"]
        assert [sent[1] for sent in adapter.sent] == [
            delivery_ledger.RECOVERED_MARKER + "phase-2 final"
        ]
        assert _wait_until(
            lambda: not restarted.session_store._db.has_post_response_barrier(
                entry.session_key, entry.session_id
            ),
            timeout=PostResponseWorker._ACTION_POLL_SECONDS * 2,
        ), "delivered recovery receipt did not drain the finalizer"
        assert restarted.session_store.get_or_create_session(source).active_turn_token is None
        assert restarted._run_agent.await_count == 0
    finally:
        _close_runner(restarted)


@pytest.mark.asyncio
async def test_terminal_barrier_blocks_real_runner_admission_only_for_its_session(
    phase2_env,
):
    """The real admission path rejects one lineage while another still runs."""
    config = GatewayConfig(
        platforms={Platform.SLACK: PlatformConfig(enabled=True, token="test-token")},
        sessions_dir=phase2_env / "sessions",
    )
    same_source = _source("C-same", "T-same")
    other_source = _source("C-other", "T-other")
    old_store = SessionStore(config.sessions_dir, config)
    runner = None
    try:
        same_entry = old_store.get_or_create_session(same_source)
        obligation_id = _seed_barrier(
            old_store._db,
            same_entry,
            actions=[
                {
                    "action_kind": "update_session",
                    "payload": {"session_key": same_entry.session_key},
                }
            ],
        )
        # Make the manual seed terminal before the fresh runner can start, so
        # an eager worker cannot consume the test's admission barrier.
        old_store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE gateway_post_response_actions SET state='blocked' "
                "WHERE obligation_id=?", (obligation_id,)
            )
        )
    finally:
        old_store.close_all_db_handles()

    adapter = CaptureSlackAdapter()
    try:
        runner = _runner(phase2_env, adapter)
        _require_post_response_worker(runner)

        await _deliver(adapter, same_source, message_id="same-next", text="must wait")
        await _deliver(adapter, other_source, message_id="other-next", text="may run")

        assert runner._run_agent.await_count == 1, (
            "the terminal barrier must stop the same-session model call while "
            "allowing an unrelated session"
        )
        assert runner._run_agent.await_args.kwargs["session_key"] == build_session_key(other_source)
        assert adapter.sent == [
            (
                "C-same",
                "⏳ This session is finishing its previous response. Please resend shortly.",
                "same-next",
                {"thread_id": "T-same", "notify": True},
            ),
            ("C-other", "phase-2 final", "other-next", {"thread_id": "T-other", "notify": True}),
        ]
    finally:
        _close_runner(runner)


def test_runner_uses_one_bounded_worker_and_shutdown_leaves_unstarted_rows_replayable(
    phase2_env, monkeypatch
):
    """Backpressure is one blocked real action, not dozens of invalid turns."""
    adapter = CaptureSlackAdapter()
    runner = None
    release_action = threading.Event()
    action_entered = threading.Event()
    preexisting_thread_ids = {thread.ident for thread in threading.enumerate()}
    try:
        runner = _runner(phase2_env, adapter)
        worker, shutdown = _require_post_response_worker(runner)
        worker_startup_threads = {
            thread.ident for thread in threading.enumerate() if thread.is_alive()
        } - preexisting_thread_ids
        assert worker_startup_threads == {worker.ident}, (
            "GatewayRunner must start exactly one long-lived post-response worker"
        )
        baseline_thread_ids = {thread.ident for thread in threading.enumerate()}
        original_update = SessionStore.update_session

        def blocking_update(store, *args, **kwargs):
            assert store is runner.session_store, "worker executed against a foreign SessionStore"
            assert threading.current_thread() is worker, (
                "post-response update must execute on the one runner-owned worker"
            )
            action_entered.set()
            assert release_action.wait(2.0), "test did not release the blocked post-response action"
            return original_update(store, *args, **kwargs)

        monkeypatch.setattr(SessionStore, "update_session", blocking_update)
        first_entry = _entry(runner, _source("C-worker-0", "T-worker-0"))
        _seed_barrier(
            runner.session_store._db,
            first_entry,
            obligation_id="worker-0",
            actions=[
                {
                    "action_kind": "update_session",
                    "payload": {"session_key": first_entry.session_key},
                }
            ],
        )
        # Direct DB setup bypasses the production receipt handoff, which
        # registers and wakes the worker after committing local work.
        runner._post_response_controller.wake()
        assert action_entered.wait(1.0), "worker did not begin the actual durable action"

        for ordinal in range(1, 9):
            entry = _entry(runner, _source(f"C-worker-{ordinal}", f"T-worker-{ordinal}"))
            _seed_barrier(
                runner.session_store._db,
                entry,
                obligation_id=f"worker-{ordinal}",
                actions=[
                    {
                        "action_kind": "update_session",
                        "payload": {"session_key": entry.session_key},
                    }
                ],
            )

        live_threads = [thread for thread in threading.enumerate() if thread.is_alive()]
        assert sum(thread is worker for thread in live_threads) == 1
        assert worker.is_alive(), "the fixed worker must remain alive while work is blocked"
        assert {thread.ident for thread in live_threads} - baseline_thread_ids == set(), (
            "post-response backpressure created detached threads instead of queuing durable rows"
        )

        started = time.monotonic()
        assert shutdown(timeout=0.2) is False, "shutdown must report a still-blocked owned worker"
        assert time.monotonic() - started <= 0.35, "bounded worker shutdown exceeded its timeout"

        release_action.set()
        assert shutdown(timeout=1.0) is True, "worker did not stop after the action was released"
        with runner.session_store._db._read_ctx() as conn:
            remaining = conn.execute(
                "SELECT obligation_id, state FROM gateway_post_response_actions "
                "WHERE obligation_id != 'worker-0' ORDER BY obligation_id"
            ).fetchall()
        assert len(remaining) == 8
        assert {row["state"] for row in remaining} <= {"pending", "failed"}, (
            "shutdown must leave unstarted actions durably replayable, not owned or dropped"
        )
        assert runner.session_store._db.has_post_response_barrier(first_entry.session_key)
    finally:
        release_action.set()
        _close_runner(runner)


def test_action_exception_records_privacy_safe_token_and_keeps_delivery_and_barrier(
    phase2_env, monkeypatch
):
    """Worker errors expose only a safe token; delivered output is never rolled back."""
    adapter = CaptureSlackAdapter()
    runner = None
    raw_exception = "customer secret: C-phase2 / phase2-child"
    try:
        runner = _runner(phase2_env, adapter)
        _require_post_response_worker(runner)
        source = _source()
        entry = _entry(runner, source)
        obligation_id = _seed_barrier(
            runner.session_store._db,
            entry,
            actions=[
                {
                    "action_kind": "rebind_session_id",
                    "payload": {
                        "session_key": entry.session_key,
                        "expected_session_id": entry.session_id,
                        "new_session_id": "phase2-child",
                    },
                }
            ],
        )
        runner.session_store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
                (obligation_id,),
            )
        )

        def raise_private_error(*_args, **_kwargs):
            raise RuntimeError(raw_exception)

        monkeypatch.setattr(runner.session_store, "rebind_session_id", raise_private_error)

        assert _wait_until(
            lambda: runner.session_store._db.get_post_response_barrier_diagnostics(
                obligation_id
            )[0]["state"] in {"failed", "blocked"}
        ), "worker did not durably record the action exception"
        diagnostic = runner.session_store._db.get_post_response_barrier_diagnostics(obligation_id)[0]
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", diagnostic["last_error"] or ""), (
            "post-response diagnostics must retain a privacy-safe error token/hash"
        )
        assert raw_exception not in diagnostic["last_error"]
        assert entry.session_key not in diagnostic["last_error"]
        assert _obligation_state(runner.session_store._db, obligation_id) == "delivered"
        assert runner.session_store._db.has_post_response_barrier(
            entry.session_key, entry.session_id
        )
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_base_consumes_exact_precommitted_obligation_without_legacy_recording(
    phase2_env, monkeypatch
):
    """Base must consume the runner receipt, not create a duplicate legacy one."""
    adapter = CaptureSlackAdapter()
    runner = None
    source = _source()
    legacy_record = Mock(wraps=delivery_ledger.record_obligation)
    try:
        runner = _runner(phase2_env, adapter)
        _require_post_response_worker(runner)
        monkeypatch.setattr(delivery_ledger, "record_obligation", legacy_record)

        message_id = "dedupe-final"
        await _deliver(adapter, source, message_id=message_id)

        obligation_id = delivery_ledger.compute_obligation_id(
            build_session_key(source), message_id, "phase-2 final"
        )
        with runner.session_store._db._read_ctx() as conn:
            rows = conn.execute(
                "SELECT obligation_id FROM delivery_obligations WHERE obligation_id=?",
                (obligation_id,),
            ).fetchall()
        assert [row["obligation_id"] for row in rows] == [obligation_id]
        assert _db_counts(runner.session_store._db) == (1, 4)
        legacy_record.assert_not_called()
    finally:
        _close_runner(runner)


def test_final_delivery_claim_and_settlement_are_single_owner_cas(phase2_env):
    """Two consumers cannot both cross the platform-send boundary."""
    config = GatewayConfig(sessions_dir=phase2_env / "sessions")
    store = SessionStore(config.sessions_dir, config)
    try:
        source = _source()
        entry = store.get_or_create_session(source)
        obligation_id = "single-owner-cas"
        store._db.create_final_delivery_and_barrier(
            obligation_id=obligation_id,
            session_key=entry.session_key,
            session_lineage=entry.session_id,
            platform="slack",
            chat_id="C-phase2",
            thread_id="T-phase2",
            content="one exact answer",
            actions=[
                {
                    "action_kind": "finalize_turn",
                    "payload": {"session_key": entry.session_key, "token": "token"},
                }
            ],
        )

        gate = threading.Barrier(2)
        results = []

        def claim_once():
            gate.wait()
            results.append(store._db.mark_final_delivery_attempting(obligation_id))

        contenders = [threading.Thread(target=claim_once) for _ in range(2)]
        for contender in contenders:
            contender.start()
        for contender in contenders:
            contender.join(timeout=2)
            assert not contender.is_alive(), "attempting CAS contender did not finish"
        assert sorted(results) == [False, True]
        with store._db._read_ctx() as conn:
            row = conn.execute(
                "SELECT state, attempts FROM delivery_obligations WHERE obligation_id=?",
                (obligation_id,),
            ).fetchone()
        assert tuple(row) == ("attempting", 1)

        private_error = "chat C-phase2 session " + entry.session_key
        assert store._db.settle_final_delivery(obligation_id, delivered=True) is True
        assert store._db.settle_final_delivery(
            obligation_id, delivered=False, error=private_error
        ) is False
        with store._db._read_ctx() as conn:
            row = conn.execute(
                "SELECT state, last_error FROM delivery_obligations WHERE obligation_id=?",
                (obligation_id,),
            ).fetchone()
        assert row["state"] == "delivered"
        assert row["last_error"] is None

        stale_id = "single-owner-stale-failed"
        store._db.create_final_delivery_and_barrier(
            obligation_id=stale_id,
            session_key=entry.session_key,
            session_lineage=entry.session_id,
            platform="slack",
            chat_id="C-phase2",
            thread_id="T-phase2",
            content="another exact answer",
            actions=[],
        )
        assert store._db.mark_final_delivery_attempting(stale_id)
        assert store._db.settle_final_delivery(
            stale_id, delivered=False, error=private_error
        )
        assert store._db.settle_final_delivery(stale_id, delivered=True) is False
        with store._db._read_ctx() as conn:
            row = conn.execute(
                "SELECT state, last_error FROM delivery_obligations WHERE obligation_id=?",
                (stale_id,),
            ).fetchone()
        assert row["state"] == "failed"
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", row["last_error"] or "")
        assert private_error not in row["last_error"]
    finally:
        store.close_all_db_handles()


def test_finalize_turn_is_not_claimable_until_delivery_is_delivered(phase2_env):
    """The durable finalizer retains the active-turn/barrier send fence."""
    config = GatewayConfig(sessions_dir=phase2_env / "sessions")
    store = SessionStore(config.sessions_dir, config)
    try:
        entry = store.get_or_create_session(_source())
        obligation_id = "finalizer-fence"
        store._db.create_final_delivery_and_barrier(
            obligation_id=obligation_id,
            session_key=entry.session_key,
            session_lineage=entry.session_id,
            platform="slack",
            chat_id="C-phase2",
            thread_id="T-phase2",
            content="answer",
            actions=[
                {
                    "action_kind": "update_session",
                    "payload": {"session_key": entry.session_key},
                },
                {
                    "action_kind": "finalize_turn",
                    "payload": {"session_key": entry.session_key, "token": "token"},
                },
            ],
        )
        first = store._db.claim_ready_post_response_actions(limit=1)
        assert [action.action_kind for action in first] == ["update_session"]
        assert store._db.complete_post_response_action(
            first[0].action_key,
            owner_pid=first[0].owner_pid,
            owner_started_at=first[0].owner_started_at,
            claimed_attempt=first[0].attempts,
        )
        assert store._db.claim_ready_post_response_actions(limit=1) == []

        assert store._db.mark_final_delivery_attempting(obligation_id)
        assert store._db.settle_final_delivery(obligation_id, delivered=False)
        assert store._db.claim_ready_post_response_actions(limit=1) == []

        # Startup recovery moves a rejected receipt back across the send
        # boundary before a positive delivery settlement releases finalization.
        store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='attempting' WHERE obligation_id=?",
                (obligation_id,),
            )
        )
        assert store._db.settle_final_delivery(obligation_id, delivered=True)
        claimed = store._db.claim_ready_post_response_actions(limit=1)
        assert [action.action_kind for action in claimed] == ["finalize_turn"]
    finally:
        store.close_all_db_handles()


@pytest.mark.asyncio
async def test_real_append_stall_does_not_hold_adapter_send_after_response_ready(
    phase2_env, monkeypatch
):
    """Gateway transcript work runs only on the durable worker, never on Base."""
    entered_append = threading.Event()
    release_append = threading.Event()
    runner = None
    worker = None
    original_append = SessionStore.append_to_transcript

    def blocking_append(store, *args, **kwargs):
        if store is runner.session_store:
            assert threading.current_thread() is worker
            entered_append.set()
            assert release_append.wait(2.0), "test did not release the durable worker"
        return original_append(store, *args, **kwargs)

    def before_send(*_args):
        assert entered_append.wait(1.0), "worker did not own transcript append"

    adapter = CaptureSlackAdapter(before_send=before_send)
    try:
        runner = _runner(phase2_env, adapter)
        worker, _shutdown = _require_post_response_worker(runner)
        monkeypatch.setattr(SessionStore, "append_to_transcript", blocking_append)
        await _deliver(adapter, _source(), message_id="append-stall")
        assert adapter.sent and not release_append.is_set()
    finally:
        release_append.set()
        _close_runner(runner)


@pytest.mark.asyncio
async def test_normal_text_handoff_is_attempting_before_the_first_output(phase2_env):
    """The ordinary final text path retains the durable pre-send fence."""
    runner = None
    source = _source()

    def before_output(kind):
        assert kind == "text"
        assert _only_obligation_row(runner.session_store._db)[2] == "attempting"

    adapter = CaptureFinalComponentsAdapter(before_output)
    try:
        runner = _runner(phase2_env, adapter)
        await _deliver(adapter, source)
        row = _only_obligation_row(runner.session_store._db)
        assert row[1] == "phase-2 final"
        assert row[2] == "delivered"
        assert adapter.outputs == ["text"]
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_result", "expected_text"),
    [
        (
            {
                "final_response": "",
                "messages": [],
                "api_calls": 1,
                "failed": True,
                "error": "upstream unavailable",
                "last_prompt_tokens": 0,
            },
            "The request failed: upstream unavailable",
        ),
        (
            {
                "final_response": "",
                "messages": [],
                "api_calls": 1,
                "failed": False,
                "last_prompt_tokens": 0,
            },
            "no response was generated",
        ),
    ],
)
async def test_normalized_visible_final_uses_handoff_and_precedes_send(
    phase2_env, agent_result, expected_text,
):
    """An empty agent result that normalizes visibly is never returned plain."""
    runner = None
    source = _source()
    observed = []

    def before_output(kind):
        assert kind == "text"
        assert _only_obligation_row(runner.session_store._db)[2] == "attempting"

    adapter = CaptureFinalComponentsAdapter(before_output)
    try:
        runner = _runner(phase2_env, adapter, result=agent_result)
        original_handler = runner._handle_message

        async def capture_handoff(event):
            result = await original_handler(event)
            observed.append(result)
            return result

        adapter.set_message_handler(capture_handoff)
        await _deliver(adapter, source)
        assert isinstance(observed[0], FinalResponseHandoff)
        assert expected_text in observed[0]
        assert _only_obligation_row(runner.session_store._db)[2] == "delivered"
        assert adapter.outputs == ["text"]
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_text_failure_does_not_settle_image_success_as_delivered(phase2_env):
    """Every intended final component must succeed, not merely one of them."""
    runner = None
    source = _source()
    adapter = ComponentOutcomeAdapter(text=False, image=True)
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "caption ![plot](https://example.test/plot.png)",
            "messages": [], "api_calls": 1, "last_prompt_tokens": 0,
        })
        await _deliver(adapter, source)
        assert "text" in adapter.outputs and "image" in adapter.outputs
        assert _only_obligation_row(runner.session_store._db)[2] == "failed"
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_text_success_does_not_settle_image_failure_as_delivered(phase2_env):
    """A later required image failure remains recoverable after text succeeds."""
    runner = None
    source = _source()
    adapter = ComponentOutcomeAdapter(text=True, image=False)
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "caption ![plot](https://example.test/plot.png)",
            "messages": [], "api_calls": 1, "last_prompt_tokens": 0,
        })
        settlements = []
        original_settle = runner._settle_final_response_handoff

        def capture_settlement(*args, **kwargs):
            settlements.append((args, kwargs))
            return original_settle(*args, **kwargs)

        runner._settle_final_response_handoff = capture_settlement
        await _deliver(adapter, source)
        assert adapter.outputs == ["text", "image"]
        assert len(settlements) == 1
        assert _only_obligation_row(runner.session_store._db)[2] == "failed"
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_partial_signal_media_batch_retains_failed_handoff_barrier(phase2_env):
    """A partial Signal attachment batch keeps the handoff recoverable.

    The predicate observes the worker after it has drained all earlier work,
    rather than relying on the scheduling race immediately after delivery.
    """
    runner = None
    source = _source()
    adapter = ComponentOutcomeAdapter(text=True, image=False)
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "caption ![plot](https://example.test/plot.png)",
            "messages": [], "api_calls": 1, "last_prompt_tokens": 0,
        })
        await _deliver(adapter, source)
        entry = _entry(runner, source)
        obligation_id = _only_obligation_row(runner.session_store._db)[0]
        assert adapter.outputs == ["text", "image"]
        assert _wait_until(
            lambda: (
                _obligation_state(runner.session_store._db, obligation_id) == "failed"
                and (
                    diagnostics := runner.session_store._db.get_post_response_barrier_diagnostics(
                        obligation_id
                    )
                )
                and diagnostics[-1]["action_kind"] == "finalize_turn"
                and diagnostics[-1]["state"] == "pending"
                and all(action["state"] == "done" for action in diagnostics[:-1])
            ),
            timeout=PostResponseWorker._ACTION_POLL_SECONDS * 4,
        ), "worker did not reach the failed-receipt finalization fence"
        assert runner.session_store._db.has_post_response_barrier(
            entry.session_key, entry.session_id
        )
        assert entry.active_turn_token is not None
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_tts_success_does_not_settle_text_caption_failure_as_delivered(
    phase2_env, monkeypatch,
):
    """A successful voice artifact cannot mask the required visible text failure."""
    runner = None
    source = _source()
    adapter = ComponentOutcomeAdapter(text=False, tts=True)
    adapter._should_auto_tts_for_chat = lambda _chat_id: True

    def fake_tts(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"audio")
        return '{"success": true, "file_path": "' + output_path + '"}'

    try:
        runner = _runner(phase2_env, adapter)
        monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)
        monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fake_tts)
        event = _event(source)
        event.message_type = MessageType.VOICE
        await adapter._process_message_background(event, build_session_key(source))
        assert "tts" in adapter.outputs and "text" in adapter.outputs
        assert _only_obligation_row(runner.session_store._db)[2] == "failed"
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_partial_local_file_failure_does_not_settle_handoff_as_delivered(phase2_env):
    """Each local attachment is a separate required component."""
    runner = None
    source = _source()
    first = "/private/host-secret/first.pdf"
    second = "/private/host-secret/second.pdf"
    adapter = ComponentOutcomeAdapter(document=True)
    adapter.extract_media = lambda _response: ([], "")
    adapter.extract_local_files = lambda _text: ([first, second], "")
    adapter.filter_local_delivery_paths = lambda paths: paths
    document_calls = 0

    async def partial_document(*args, **kwargs):
        nonlocal document_calls
        document_calls += 1
        adapter._before_output("document")
        adapter.outputs.append("document")
        return SendResult(success=document_calls == 1, error="second rejected")

    adapter.send_document = partial_document
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "attached files",
            "messages": [], "api_calls": 1, "last_prompt_tokens": 0,
        })
        await _deliver(adapter, source)
        row = _only_obligation_row(runner.session_store._db)
        assert adapter.outputs.count("document") == 2
        assert row[2] == "failed"
        assert first not in row[1] and second not in row[1]
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_tts_caption_first_handoff_is_attempting_before_audio_output(
    phase2_env, monkeypatch
):
    """Voice/TTS captions must not cross the send boundary before the receipt."""
    runner = None
    source = _source()

    def before_output(kind):
        assert kind == "tts"
        assert _only_obligation_row(runner.session_store._db)[2] == "attempting"

    adapter = CaptureFinalComponentsAdapter(before_output)
    adapter.platform = Platform.TELEGRAM
    adapter._should_auto_tts_for_chat = lambda _chat_id: True

    def fake_tts(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"audio")
        return '{"success": true, "file_path": "' + output_path + '"}'

    try:
        runner = _runner(phase2_env, adapter)
        monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)
        monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fake_tts)
        event = _event(source)
        event.message_type = MessageType.VOICE
        await adapter._process_message_background(event, build_session_key(source))
        row = _only_obligation_row(runner.session_store._db)
        assert row[1] == "phase-2 final"
        assert row[2] == "delivered"
        assert adapter.outputs == ["tts"]
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_media_only_handoff_persists_safe_recovery_notice_and_settles(phase2_env):
    """Attachment-only replies get a redeliverable notice, never a host path."""
    runner = None
    source = _source()
    raw_path = "/private/host-secret/generated/report.pdf"

    def before_output(kind):
        assert kind == "document"
        assert _only_obligation_row(runner.session_store._db)[2] == "attempting"

    adapter = CaptureFinalComponentsAdapter(before_output)
    adapter.extract_media = lambda _response: ([(raw_path, False)], "")
    adapter.filter_media_delivery_paths = lambda media: media
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "MEDIA:" + raw_path,
            "messages": [],
            "api_calls": 1,
            "last_prompt_tokens": 0,
        })
        await _deliver(adapter, source)
        row = _only_obligation_row(runner.session_store._db)
        assert row[1] == "⚠️ The gateway was interrupted while delivering an attachment response. Please ask me to resend it."
        assert raw_path not in row[1]
        assert row[2] == "delivered"
        assert adapter.outputs == ["document"]
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_image_only_handoff_has_receipt_and_settles_from_image_success(phase2_env):
    """Native image-only delivery is a final component, not an untracked side path."""
    runner = None
    source = _source()

    def before_output(kind):
        assert kind == "image"
        assert _only_obligation_row(runner.session_store._db)[2] == "attempting"

    adapter = CaptureFinalComponentsAdapter(before_output)
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "![plot](https://example.test/plot.png)",
            "messages": [],
            "api_calls": 1,
            "last_prompt_tokens": 0,
        })
        await _deliver(adapter, source)
        row = _only_obligation_row(runner.session_store._db)
        assert "attachment response" in row[1]
        assert "https://" not in row[1]
        assert row[2] == "delivered"
        assert adapter.outputs == ["image"]
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_precommit_failure_suppresses_each_final_output_surface(phase2_env, monkeypatch):
    """A handoff receipt/CAS failure must fail closed for text, TTS, and files."""
    runner = None
    source = _source()
    raw_path = "/private/host-secret/generated/report.pdf"
    adapter = CaptureFinalComponentsAdapter()
    adapter.extract_media = lambda _response: (
        [(raw_path, False)],
        "phase-2 final ![plot](https://example.test/plot.png)",
    )
    adapter.filter_media_delivery_paths = lambda media: media
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "phase-2 final MEDIA:" + raw_path + " ![plot](https://example.test/plot.png)",
            "messages": [],
            "api_calls": 1,
            "last_prompt_tokens": 0,
        })
        monkeypatch.setattr(
            runner.session_store._db,
            "create_final_delivery_and_barrier",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected failure")),
        )
        await _deliver(adapter, source)
        assert adapter.outputs == []
        assert adapter.sent == []
        assert _db_counts(runner.session_store._db) == (0, 0)
        assert _entry(runner, source).active_turn_token, (
            "a receipt transaction failure must retain active-turn recovery evidence"
        )
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_precommit_failure_suppresses_tts_and_caption_output(phase2_env, monkeypatch):
    """The voice-first route observes the same fail-closed receipt gate."""
    runner = None
    source = _source()
    adapter = CaptureFinalComponentsAdapter()
    adapter.platform = Platform.TELEGRAM
    adapter._should_auto_tts_for_chat = lambda _chat_id: True

    def fake_tts(*, output_path, **_kwargs):
        Path(output_path).write_bytes(b"audio")
        return '{"success": true, "file_path": "' + output_path + '"}'

    try:
        runner = _runner(phase2_env, adapter)
        monkeypatch.setattr("tools.tts_tool.check_tts_requirements", lambda: True)
        monkeypatch.setattr("tools.tts_tool.text_to_speech_tool", fake_tts)
        monkeypatch.setattr(
            runner.session_store._db,
            "create_final_delivery_and_barrier",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected failure")),
        )
        event = _event(source)
        event.message_type = MessageType.VOICE
        await adapter._process_message_background(event, build_session_key(source))
        assert adapter.outputs == []
        assert adapter.sent == []
        assert _entry(runner, source).active_turn_token
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_attachment_aggregate_failure_settles_handoff_as_failed(phase2_env):
    """A handoff stays recoverable when no intended attachment succeeds."""
    runner = None
    source = _source()
    raw_path = "/private/host-secret/generated/report.pdf"
    adapter = CaptureFinalComponentsAdapter()
    adapter.extract_media = lambda _response: ([(raw_path, False)], "")
    adapter.filter_media_delivery_paths = lambda media: media

    async def failed_document(*_args, **_kwargs):
        adapter.outputs.append("document")
        return SendResult(success=False, error="rejected")

    adapter.send_document = failed_document
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "MEDIA:" + raw_path,
            "messages": [],
            "api_calls": 1,
            "last_prompt_tokens": 0,
        })
        await _deliver(adapter, source)
        assert _only_obligation_row(runner.session_store._db)[2] == "failed"
        assert adapter.outputs == ["document", "text"]
        assert all("attachment response" not in message[1] for message in adapter.sent)
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_empty_extracted_handoff_explicitly_cancels_its_active_turn(phase2_env):
    """A stale/empty uncommitted handoff cannot strand an active-turn token."""
    runner = None
    source = _source()
    adapter = CaptureFinalComponentsAdapter()
    adapter.extract_media = lambda _response: ([], "")
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "MEDIA:/not-a-real-delivery.pdf",
            "messages": [],
            "api_calls": 1,
            "last_prompt_tokens": 0,
        })
        await _deliver(adapter, source)
        assert adapter.outputs == []
        assert _db_counts(runner.session_store._db) == (0, 0)
        assert _entry(runner, source).active_turn_token is None
    finally:
        _close_runner(runner)


def test_worker_starts_after_runner_is_fully_initialized_without_claiming_pending_work(
    phase2_env, monkeypatch
):
    """Startup replay cannot dispatch or spend an attempt during construction."""
    config = GatewayConfig(sessions_dir=phase2_env / "sessions")
    source = _source("C-startup", "T-startup")
    old_store = SessionStore(config.sessions_dir, config)
    try:
        entry = old_store.get_or_create_session(source)
        _seed_barrier(
            old_store._db,
            entry,
            obligation_id="startup-construction-pending",
            actions=[
                {
                    "action_kind": "update_session",
                    "payload": {"session_key": entry.session_key},
                }
            ],
        )
    finally:
        old_store.close_all_db_handles()

    observed = {}

    class ProbeWorker:
        def __init__(self, runner):
            self.runner = runner

        def start(self):
            # These are consumed by durable dispatch paths and must all exist
            # before the startup replay thread is exposed.
            assert self.runner.delivery_router is not None
            assert self.runner._agent_cache_lock is not None
            assert self.runner._session_db_handles_lock is not None
            with self.runner.session_store._db._read_ctx() as conn:
                observed["attempts"] = conn.execute(
                    "SELECT attempts FROM gateway_post_response_actions "
                    "WHERE obligation_id='startup-construction-pending'"
                ).fetchone()[0]
            return threading.Thread(name="probe-post-response")

        def wake(self):
            return None

        def shutdown(self, _timeout):
            return True

    monkeypatch.setattr("gateway.post_response.PostResponseWorker", ProbeWorker)
    runner = GatewayRunner(config)
    try:
        assert observed == {"attempts": 0}
    finally:
        _close_runner(runner)


def test_non_compressing_handoff_is_minimal_and_omits_huge_skip_db_payload():
    """No-op agent mirrors and no-op route/topic actions are never serialized."""
    huge_agent_tool_payload = "secret-tool-output-" + ("x" * 200_000)
    handoff = FinalResponseHandoff(
        "ordinary final",
        runner=SimpleNamespace(session_store=None),
        session_key="private-key",
        session_lineage="current-session",
        expected_session_id="current-session",
        new_session_id=None,
        active_turn_token="token",
        last_prompt_tokens=3,
        touch_activity=True,
        append_final_text=False,
        clear_resume_pending=False,
        gateway_actions=[
            {
                "action_kind": "append_to_transcript",
                "payload": {
                    "session_id": "current-session",
                    "skip_db": True,
                    "message": {"role": "tool", "content": huge_agent_tool_payload},
                },
            }
        ],
        source_data={"platform": "telegram", "chat_id": "C", "thread_id": "T"},
    )

    actions = handoff.actions_for_text("ordinary final")
    assert [action["action_kind"] for action in actions] == [
        "update_session",
        "finalize_turn",
    ]
    assert huge_agent_tool_payload not in repr(actions)


@pytest.mark.asyncio
async def test_legacy_none_image_batch_override_settles_image_only_handoff(phase2_env):
    """A successful historical ``None`` batch result is attempted delivery."""
    runner = None
    source = _source("C-none-images", "T-none-images")
    adapter = LegacyNoneImageBatchAdapter()
    try:
        runner = _runner(phase2_env, adapter, result={
            "final_response": "![plot](https://example.test/none.png)",
            "messages": [],
            "api_calls": 1,
            "last_prompt_tokens": 0,
        })
        await _deliver(adapter, source)
        obligation_id, _content, state = _only_obligation_row(runner.session_store._db)
        assert adapter.outputs == ["image"]
        assert state == "delivered"
        assert _wait_until(
            lambda: not runner.session_store._db.has_post_response_barrier(
                build_session_key(source), _entry(runner, source).session_id
            )
        ), obligation_id
    finally:
        _close_runner(runner)


@pytest.mark.asyncio
async def test_media_only_missing_message_ids_get_distinct_safe_handoff_obligations(phase2_env):
    """Recovery text is safe and constant, but source handoffs remain distinct."""
    runner = None
    source = _source("C-media-identity", "T-media-identity")
    first_path = "/private/secret/first.png"
    second_path = "/private/secret/second.png"
    adapter = CaptureFinalComponentsAdapter()
    adapter.extract_media = lambda response: ([(response.removeprefix("MEDIA:"), False)], "")
    adapter.filter_media_delivery_paths = lambda media: media
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


def test_false_rebind_keeps_barrier_and_never_runs_later_transcript_action(
    phase2_env, monkeypatch
):
    """A false CAS is only idempotent when the target route already exists."""
    runner = None
    source = _source("C-false-rebind", "T-false-rebind")
    appended = []
    try:
        runner = _runner(phase2_env, CaptureSlackAdapter())
        entry = _entry(runner, source)
        obligation_id = _seed_barrier(
            runner.session_store._db,
            entry,
            obligation_id="false-rebind",
            actions=[
                {
                    "action_kind": "rebind_session_id",
                    "payload": {
                        "session_key": entry.session_key,
                        "expected_session_id": entry.session_id,
                        "new_session_id": "unreached-child",
                    },
                },
                {
                    "action_kind": "append_to_transcript",
                    "payload": {
                        "session_id": "unreached-child",
                        "message": {"role": "assistant", "content": "must not append"},
                    },
                },
            ],
        )
        runner.session_store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
                (obligation_id,),
            )
        )
        monkeypatch.setattr(runner.session_store, "rebind_session_id", lambda *_a: False)
        monkeypatch.setattr(
            runner.session_store,
            "append_to_transcript",
            lambda *_a, **_k: appended.append(True),
        )
        runner._post_response_controller.wake()
        assert _wait_until(lambda: runner.session_store._db.get_post_response_barrier_diagnostics(
            obligation_id
        )[0]["state"] in {"failed", "blocked"})
        assert appended == []
    finally:
        _close_runner(runner)


def test_non_durable_transcript_append_keeps_barrier_and_blocks_update(
    phase2_env, monkeypatch
):
    """A spooled/no-op append is not enough to mark a durable action complete."""
    runner = None
    source = _source("C-false-append", "T-false-append")
    updates = []
    try:
        runner = _runner(phase2_env, CaptureSlackAdapter())
        entry = _entry(runner, source)
        obligation_id = _seed_barrier(
            runner.session_store._db,
            entry,
            obligation_id="false-append",
            actions=[
                {
                    "action_kind": "append_to_transcript",
                    "payload": {
                        "session_id": entry.session_id,
                        "message": {"role": "assistant", "content": "must persist"},
                    },
                },
                {
                    "action_kind": "update_session",
                    "payload": {"session_key": entry.session_key, "last_prompt_tokens": 99},
                },
            ],
        )
        runner.session_store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
                (obligation_id,),
            )
        )
        monkeypatch.setattr(runner.session_store, "append_to_transcript", lambda *_a, **_k: None)
        monkeypatch.setattr(
            runner.session_store,
            "update_session",
            lambda *_a, **_k: updates.append(True),
        )
        runner._post_response_controller.wake()
        assert _wait_until(lambda: runner.session_store._db.get_post_response_barrier_diagnostics(
            obligation_id
        )[0]["state"] in {"failed", "blocked"})
        assert updates == []
    finally:
        _close_runner(runner)


def test_worker_appends_metadata_rich_terminal_row_after_same_role_mismatch(
    phase2_env,
):
    """A same-role tail is not a receipt for a different persisted row."""
    runner = None
    source = _source("C-terminal-row-fields", "T-terminal-row-fields")
    intended = {
        "role": "session_meta",
        "timestamp": 2_000.25,
        "display_metadata": {"origin": "intended", "tools_version": 2},
    }
    try:
        runner = _runner(phase2_env, CaptureSlackAdapter())
        entry = _entry(runner, source)
        runner.session_store.append_to_transcript(
            entry.session_id,
            {
                "role": "session_meta",
                "timestamp": 1_000.5,
                "display_metadata": {"origin": "other", "tools_version": 1},
            },
        )
        obligation_id = _seed_barrier(
            runner.session_store._db,
            entry,
            obligation_id="terminal-row-fields",
            actions=[
                {
                    "action_kind": "append_to_transcript",
                    "payload": {"session_id": entry.session_id, "message": intended},
                }
            ],
        )
        runner.session_store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
                (obligation_id,),
            )
        )
        runner._post_response_controller.wake()
        assert _wait_until(
            lambda: not runner.session_store._db.has_post_response_barrier(
                entry.session_key, entry.session_id
            )
        )

        rows = runner.session_store._db.get_messages(entry.session_id)
        assert [row["role"] for row in rows] == ["session_meta", "session_meta"]
        assert rows[-1]["timestamp"] == intended["timestamp"]
        assert rows[-1]["display_metadata"] == intended["display_metadata"]

        # A reclaimed action whose terminal row is truly identical must not
        # append another copy.
        duplicate_id = _seed_barrier(
            runner.session_store._db,
            entry,
            obligation_id="terminal-row-fields-idempotent",
            actions=[
                {
                    "action_kind": "append_to_transcript",
                    "payload": {"session_id": entry.session_id, "message": intended},
                }
            ],
        )
        runner.session_store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
                (duplicate_id,),
            )
        )
        runner._post_response_controller.wake()
        assert _wait_until(
            lambda: not runner.session_store._db.has_post_response_barrier(
                entry.session_key, entry.session_id
            )
        )
        assert len(runner.session_store._db.get_messages(entry.session_id)) == 2
    finally:
        _close_runner(runner)


def test_false_finalize_keeps_requested_active_token_and_barrier(phase2_env, monkeypatch):
    """A false active-turn CAS cannot silently complete the terminal action."""
    runner = None
    source = _source("C-false-finalize", "T-false-finalize")
    try:
        runner = _runner(phase2_env, CaptureSlackAdapter())
        entry = _entry(runner, source)
        token = runner.session_store.mark_turn_active(entry.session_key)
        obligation_id = _seed_barrier(
            runner.session_store._db,
            entry,
            obligation_id="false-finalize",
            actions=[
                {
                    "action_kind": "finalize_turn",
                    "payload": {
                        "session_key": entry.session_key,
                        "active_turn_token": token,
                        "session_id": entry.session_id,
                    },
                }
            ],
        )
        runner.session_store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
                (obligation_id,),
            )
        )
        monkeypatch.setattr(runner.session_store, "clear_turn_active", lambda *_a: False)
        runner._post_response_controller.wake()
        assert _wait_until(lambda: runner.session_store._db.get_post_response_barrier_diagnostics(
            obligation_id
        )[0]["state"] in {"failed", "blocked"})
        assert _entry(runner, source).active_turn_token == token
    finally:
        _close_runner(runner)


def test_noop_update_keeps_barrier_and_blocks_later_action(phase2_env, monkeypatch):
    """A metadata helper cannot silently acknowledge a failed durable write."""
    runner = None
    source = _source("C-false-update", "T-false-update")
    appended = []
    try:
        runner = _runner(phase2_env, CaptureSlackAdapter())
        entry = _entry(runner, source)
        obligation_id = _seed_barrier(
            runner.session_store._db,
            entry,
            obligation_id="false-update",
            actions=[
                {
                    "action_kind": "update_session",
                    "payload": {"session_key": entry.session_key, "last_prompt_tokens": 404},
                },
                {
                    "action_kind": "append_to_transcript",
                    "payload": {
                        "session_id": entry.session_id,
                        "message": {"role": "assistant", "content": "must not follow noop"},
                    },
                },
            ],
        )
        runner.session_store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
                (obligation_id,),
            )
        )
        monkeypatch.setattr(runner.session_store, "update_session", lambda *_a, **_k: None)
        monkeypatch.setattr(
            runner.session_store,
            "append_to_transcript",
            lambda *_a, **_k: appended.append(True),
        )
        runner._post_response_controller.wake()
        assert _wait_until(lambda: runner.session_store._db.get_post_response_barrier_diagnostics(
            obligation_id
        )[0]["state"] in {"failed", "blocked"})
        assert appended == []
    finally:
        _close_runner(runner)


def test_noop_topic_sync_keeps_barrier(phase2_env, monkeypatch):
    """A swallowed Telegram topic write is not a completed durable action."""
    runner = None
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="C-topic-noop",
        chat_type="dm",
        thread_id="T-topic-noop",
        user_id="U-topic-noop",
    )
    try:
        runner = _runner(phase2_env, CaptureSlackAdapter())
        runner.session_store._db.enable_telegram_topic_mode(
            chat_id=source.chat_id, user_id=source.user_id
        )
        entry = _entry(runner, source)
        obligation_id = _seed_barrier(
            runner.session_store._db,
            entry,
            obligation_id="false-topic-sync",
            actions=[
                {
                    "action_kind": "sync_topic_binding",
                    "payload": {"session_key": entry.session_key, "source": source.to_dict()},
                }
            ],
        )
        runner.session_store._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
                (obligation_id,),
            )
        )
        monkeypatch.setattr(runner, "_sync_telegram_topic_binding", lambda *_a, **_k: None)
        runner._post_response_controller.wake()
        assert _wait_until(lambda: runner.session_store._db.get_post_response_barrier_diagnostics(
            obligation_id
        )[0]["state"] in {"failed", "blocked"})
    finally:
        _close_runner(runner)
