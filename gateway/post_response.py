"""Durable, ordered post-response work for one gateway runner.

The SQLite action table is the queue.  This module deliberately has no
unbounded in-memory queue: its Event merely wakes a single daemon worker after
new work is committed (and the worker periodically rescans after a restart).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
import threading
import time
import weakref
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from hermes_constants import (
    get_process_hermes_home,
    reset_hermes_home_override,
    set_hermes_home_override,
)
from agent.memory_provider import spawn_context_thread
from hermes_state import PostResponseAction
from hermes_state_messages import _scrub_surrogates

logger = logging.getLogger(__name__)


class FinalResponseHandoff(str):
    """A string-compatible final response carrying its durable action plan.

    Keeping this a ``str`` preserves every existing adapter extraction and
    media path.  The adapter consumes the extra attributes just before its
    final text send.
    """

    def __new__(
        cls,
        text: str,
        *,
        runner: Any,
        session_key: str,
        session_lineage: str,
        expected_session_id: str,
        new_session_id: Optional[str],
        active_turn_token: Optional[str],
        last_prompt_tokens: int,
        touch_activity: bool,
        append_final_text: bool,
        clear_resume_pending: bool,
        gateway_actions: Optional[Iterable[dict[str, Any]]] = None,
        source_data: Optional[dict[str, Any]] = None,
    ):
        value = super().__new__(cls, text)
        value.runner = runner
        value.session_key = session_key
        value.session_lineage = session_lineage
        value.expected_session_id = expected_session_id
        value.new_session_id = new_session_id
        value.active_turn_token = active_turn_token
        value.last_prompt_tokens = last_prompt_tokens
        value.touch_activity = touch_activity
        value.append_final_text = append_final_text
        value.clear_resume_pending = clear_resume_pending
        value.gateway_actions = [dict(action) for action in (gateway_actions or ())]
        value.source_data = source_data or None
        value.obligation_id = None
        value.receipt_attempted = False
        # Hash the original, complete final handoff while it is still in
        # process memory.  This lets media-only replies share safe constant
        # recovery content without collapsing distinct missing-message-id
        # deliveries, and never persists a local media path or URL directly.
        identity_payload = {
            "response": str(text),
            "session_key": session_key,
            "expected_session_id": expected_session_id,
            "new_session_id": new_session_id,
            "active_turn_token": active_turn_token,
            "gateway_actions": value.gateway_actions,
        }
        value.delivery_identity = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, default=str, separators=(",", ":")).encode(
                "utf-8", "replace"
            )
        ).hexdigest()
        # Delivery is deliberately deferred to an adapter ``to_thread`` call.
        # That call normally inherits contextvars, but a durable receipt must
        # never rely on that incidental propagation: capture the exact state
        # DB while the producing turn still owns its profile scope.
        value.post_response_db_path = None
        value.post_response_home = None
        try:
            db = getattr(getattr(runner, "session_store", None), "_db", None)
            db_path = getattr(db, "db_path", None)
            if db_path:
                value.post_response_db_path = Path(db_path).absolute()
                value.post_response_home = value.post_response_db_path.parent
        except (AttributeError, TypeError, ValueError, OSError):
            # Existing fake stores can omit a concrete SessionDB. The receipt
            # path will use its active store as the compatibility fallback.
            pass
        return value

    def actions_for_text(self, text_content: str) -> list[dict[str, Any]]:
        """Build the restricted, replay-idempotent action plan for final text."""
        actions: list[dict[str, Any]] = []
        target_session_id = self.expected_session_id
        rebind_target = self.new_session_id or self.expected_session_id
        session_changed = rebind_target != self.expected_session_id
        if session_changed:
            actions.append(
                {
                    "action_kind": "rebind_session_id",
                    "payload": {
                        "session_key": self.session_key,
                        "expected_session_id": self.expected_session_id,
                        "new_session_id": rebind_target,
                        "source": self.source_data,
                    },
                }
            )
            target_session_id = rebind_target
            if self.source_data and self.source_data.get("platform") == "telegram":
                try:
                    from gateway.session import SessionSource

                    is_topic_lane = bool(
                        getattr(self.runner, "_is_telegram_topic_lane", lambda _source: False)(
                            SessionSource.from_dict(self.source_data)
                        )
                    )
                except Exception:  # health: allow BLE001 -- Optional adapter capability callback failure must leave the durable generic receipt valid.
                    is_topic_lane = False
                if is_topic_lane:
                    actions.append(
                        {
                            "action_kind": "sync_topic_binding",
                            "payload": {
                                "session_key": self.session_key,
                                "source": self.source_data,
                            },
                        }
                    )
        for action in self.gateway_actions:
            payload = dict(action.get("payload", {}))
            if action.get("action_kind") == "append_to_transcript" and payload.get("skip_db"):
                # SessionStore deliberately does nothing for skip_db=True;
                # retaining the payload creates a durable privacy/bloat risk
                # without producing a replayable effect.
                continue
            if payload.get("session_id") == self.expected_session_id:
                payload["session_id"] = target_session_id
            actions.append({"action_kind": action["action_kind"], "payload": payload})
        if self.append_final_text and text_content:
            actions.append({
                "action_kind": "append_to_transcript",
                "payload": {
                    "session_id": target_session_id,
                    "message": {"role": "assistant", "content": text_content},
                },
            })
        actions.append(
            {
                "action_kind": "update_session",
                "payload": {
                    "session_key": self.session_key,
                    "last_prompt_tokens": self.last_prompt_tokens,
                    "touch_activity": self.touch_activity,
                    "clear_resume_pending": self.clear_resume_pending,
                },
            }
        )
        if self.clear_resume_pending:
            actions.append(
                {
                    "action_kind": "clear_restart_failure_count",
                    "payload": {"session_key": self.session_key},
                }
            )
        actions.append(
            {
                "action_kind": "finalize_turn",
                "payload": {
                    "session_key": self.session_key,
                    "active_turn_token": self.active_turn_token,
                    "session_id": target_session_id,
                },
            }
        )
        return actions


class PostResponseWorker:
    """One bounded/coalescing DB-authoritative post-response worker."""

    # A local receipt commit registers its exact DB and calls ``wake()``, so
    # steady-state polling is only recovery/discovery insurance.  Keep those
    # fallbacks deliberately sparse; wake-driven work still drains promptly.
    _ACTION_POLL_SECONDS = 1.0
    _DISCOVERY_SECONDS = 10.0
    _MAX_DB_PATHS = 128

    def __init__(self, runner: Any) -> None:
        # The worker is runner-owned, not merely SessionStore-owned: terminal
        # work must re-baseline the exact cached AIAgent that produced this
        # turn.  Keeping the store as a derived reference also makes that
        # ownership obvious to callers and tests.
        self._runner_ref = weakref.ref(runner)
        self.store = runner.session_store
        self._wake = threading.Event()
        self._stop = threading.Event()
        # Capture the runner's actual root handle.  In production this is the
        # process HERMES_HOME; taking the handle path also preserves explicit
        # SessionDB pins used by recovery/test harnesses.
        initial_db = self.store._db
        initial_path = getattr(initial_db, "db_path", None)
        if initial_path is not None:
            self._root_db_path = Path(initial_path).absolute()
            self._root_home = self._root_db_path.parent
        else:
            self._root_home = Path(get_process_hermes_home()).absolute()
            self._root_db_path = self._root_home / "state.db"
        self._db_paths: OrderedDict[Path, None] = OrderedDict()
        self._db_paths_lock = threading.Lock()
        self._round_robin_index = 0
        self._capacity_warning_emitted = False
        self.register_db_path(self._root_db_path)
        self._discover_db_paths()
        self.thread = spawn_context_thread(
            target=self._run,
            name="gateway-post-response",
            daemon=True,
        )

    def start(self) -> threading.Thread:
        self.thread.start()
        self.wake()
        return self.thread

    @property
    def runner(self) -> Any | None:
        return self._runner_ref()

    def wake(self) -> None:
        self._wake.set()

    def register_db_path(self, db_path: str | Path) -> bool:
        """Register one receipt DB for the sole worker, if it is in scope.

        The set is intentionally bounded and coalesced by canonical absolute
        path.  It is only a wake/scheduling aid; SQLite rows remain the queue.
        """
        path = self._validated_db_path(db_path)
        if path is None:
            return False
        with self._db_paths_lock:
            capacity = max(1, int(self._MAX_DB_PATHS))
            if path in self._db_paths:
                self._db_paths.pop(path)
            elif len(self._db_paths) >= capacity:
                # The root DB is the worker's immutable recovery anchor.  Do
                # not rotate it forever when the bounded profile set is full:
                # evict the oldest profile deterministically instead.  The
                # receipt row remains durable and a later discovery pass can
                # register it again.
                oldest_profile = next(
                    (candidate for candidate in self._db_paths if candidate != self._root_db_path),
                    None,
                )
                if oldest_profile is None:
                    if not self._capacity_warning_emitted:
                        logger.warning(
                            "post-response DB path capacity reached; profile work will be retried by discovery"
                        )
                        self._capacity_warning_emitted = True
                    return False
                self._db_paths.pop(oldest_profile)
                if not self._capacity_warning_emitted:
                    logger.warning(
                        "post-response DB path capacity reached; evicted oldest profile from scheduling"
                    )
                    self._capacity_warning_emitted = True
            self._db_paths[path] = None
        return True

    def _validated_db_path(self, db_path: str | Path) -> Optional[Path]:
        """Accept root or one direct, non-symlinked profile ``state.db``."""
        try:
            path = Path(db_path).absolute()
        except (TypeError, ValueError, OSError):
            return None
        if path == self._root_db_path:
            return path
        profiles_root = self._root_home / "profiles"
        if path.name != "state.db" or path.parent.parent != profiles_root:
            return None
        try:
            profiles_stat = os.lstat(profiles_root)
            if stat.S_ISLNK(profiles_stat.st_mode) or not stat.S_ISDIR(profiles_stat.st_mode):
                return None
            profile_stat = os.lstat(path.parent)
            if stat.S_ISLNK(profile_stat.st_mode) or not stat.S_ISDIR(profile_stat.st_mode):
                return None
            if path.exists():
                db_stat = os.lstat(path)
                if stat.S_ISLNK(db_stat.st_mode) or not stat.S_ISREG(db_stat.st_mode):
                    return None
        except OSError:
            return None
        return path

    def _discover_db_paths(self) -> None:
        """Discover root plus direct profile DBs without walking symlinks."""
        self.register_db_path(self._root_db_path)
        profiles_root = self._root_home / "profiles"
        try:
            root_stat = os.lstat(profiles_root)
            if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
                return
            children = list(profiles_root.iterdir())
        except OSError:
            return
        for profile_home in children:
            try:
                profile_stat = os.lstat(profile_home)
            except OSError:
                continue
            if stat.S_ISLNK(profile_stat.st_mode) or not stat.S_ISDIR(profile_stat.st_mode):
                continue
            db_path = profile_home / "state.db"
            try:
                db_stat = os.lstat(db_path)
            except OSError:
                continue
            if stat.S_ISLNK(db_stat.st_mode) or not stat.S_ISREG(db_stat.st_mode):
                continue
            self.register_db_path(db_path)

    def _round_robin_db_paths(self) -> list[Path]:
        with self._db_paths_lock:
            paths = list(self._db_paths)
            if not paths:
                return []
            start = self._round_robin_index % len(paths)
            self._round_robin_index = (start + 1) % len(paths)
        return paths[start:] + paths[:start]

    @contextmanager
    def _db_scope(self, db_path: Path):
        """Install the receipt parent as the worker thread's active profile."""
        token = set_hermes_home_override(str(db_path.parent))
        try:
            db = self.store._db
            actual_path = getattr(db, "db_path", None)
            if actual_path is None or Path(actual_path).absolute() != db_path:
                raise RuntimeError("post-response DB scope mismatch")
            yield db
        finally:
            reset_hermes_home_override(token)

    def shutdown(self, timeout: float) -> bool:
        self._stop.set()
        self._wake.set()
        # Leave a small scheduler margin to honour the caller's aggregate
        # shutdown budget rather than consuming it all in Thread.join().
        if self.thread.ident is None:
            return True
        self.thread.join(max(0.0, float(timeout) - 0.05))
        return not self.thread.is_alive()

    @staticmethod
    def _error_token(exc: BaseException) -> str:
        # Do not leak action payloads, session keys, provider text, or platform
        # identifiers into retry diagnostics.  A stable hash is sufficient to
        # correlate repeated failures.
        material = f"{type(exc).__name__}:{exc}".encode("utf-8", "replace")
        return "sha256:" + hashlib.sha256(material).hexdigest()

    def _run(self) -> None:
        # ``__init__`` performs the startup scan before the thread is exposed.
        # Thereafter local commits wake us immediately; fallback action polling
        # and profile discovery are monotonic, low-frequency recovery paths.
        next_action_poll_at = time.monotonic()
        next_discovery_at = time.monotonic() + self._DISCOVERY_SECONDS
        draining = False
        while not self._stop.is_set():
            if self.runner is None:
                # Test/bare runners are often short lived.  Do not let their
                # daemon retain a stale store forever; uncompleted rows stay
                # durable for the next real runner.
                return
            now = time.monotonic()
            woke = self._wake.is_set()
            if woke:
                self._wake.clear()
            discovered = False
            if now >= next_discovery_at:
                self._discover_db_paths()
                next_discovery_at = time.monotonic() + self._DISCOVERY_SECONDS
                discovered = True
            should_poll = woke or discovered or draining or now >= next_action_poll_at
            if not should_poll:
                deadline = min(next_action_poll_at, next_discovery_at)
                self._wake.wait(max(0.0, deadline - time.monotonic()))
                continue
            claimed = None
            for db_path in self._round_robin_db_paths():
                try:
                    with self._db_scope(db_path) as db:
                        actions = db.claim_ready_post_response_actions(limit=1)
                except Exception as exc:  # health: allow BLE001 -- Worker retries arbitrary scoped store failures without exposing action payloads in tracebacks.
                    logger.warning(
                        "post-response action claim failed (%s)", type(exc).__name__, exc_info=False
                    )
                    continue
                if actions:
                    claimed = (db_path, actions[0])
                    break
            if claimed is None:
                # A profile can be created by another process between
                # discovery passes.  Local commits do not wait for that pass:
                # they register the DB and wake this worker.
                draining = False
                next_action_poll_at = time.monotonic() + self._ACTION_POLL_SECONDS
                continue
            db_path, action = claimed
            try:
                with self._db_scope(db_path):
                    self._dispatch(action)
            except Exception as exc:  # health: allow BLE001 -- Durable action callbacks may fail arbitrarily; persist redacted failure and keep the barrier replayable.
                try:
                    with self._db_scope(db_path) as db:
                        db.fail_post_response_action(
                            action.action_key,
                            claimed_attempt=action.claim_attempt,
                            owner_pid=action.owner_pid,
                            owner_started_at=action.owner_started_at,
                            error=self._error_token(exc),
                        )
                except Exception:  # health: allow BLE001 -- A terminal checkpoint failure must preserve action ownership for replay; diagnostics omit private payloads.
                    logger.warning("post-response action failure could not be recorded", exc_info=False)
            else:
                try:
                    # A bounded shutdown never publishes an action that was
                    # executing when stop was requested. Leaving its durable
                    # running ownership intact makes a crash/process death
                    # reclaim it; this worker must not start a later row.
                    if self._stop.is_set():
                        return
                    with self._db_scope(db_path) as db:
                        db.complete_post_response_action(
                            action.action_key,
                            claimed_attempt=action.claim_attempt,
                            owner_pid=action.owner_pid,
                            owner_started_at=action.owner_started_at,
                        )
                except Exception:  # health: allow BLE001 -- A terminal checkpoint failure must preserve action ownership for replay; diagnostics omit private payloads.
                    logger.warning("post-response action completion could not be recorded", exc_info=False)
            # Drain immediately after useful work.  Round-robin path ordering
            # advances on every claim pass, so a busy profile cannot starve a
            # second ready profile after one shared wake.
            draining = True

    _ACTION_HANDLERS = {
        "rebind_session_id": "_apply_rebind_session_id",
        "append_to_transcript": "_apply_append_to_transcript",
        "update_session": "_apply_update_session",
        "clear_restart_failure_count": "_apply_clear_restart_failure_count",
        "sync_topic_binding": "_apply_sync_topic_binding",
        "finalize_turn": "_apply_finalize_turn",
        "clear_resume_pending": "_apply_clear_resume_pending",
        "clear_active_turn": "_apply_clear_active_turn",
    }

    def _dispatch(self, action: PostResponseAction) -> None:
        """Execute only the explicitly supported durable action kinds."""
        method = self._ACTION_HANDLERS.get(action.action_kind)
        if method is None:
            raise ValueError("unsupported post-response action kind")
        payload = action.payload if isinstance(action.payload, dict) else {}
        getattr(self, method)(action, payload)

    def _apply_rebind_session_id(self, _action: PostResponseAction, payload: dict[str, Any]) -> None:
        session_key = str(payload["session_key"])
        new_session_id = str(payload["new_session_id"])
        rebound = self.store.rebind_session_id(
            session_key,
            str(payload["expected_session_id"]),
            new_session_id,
        )
        if not rebound and self.store.peek_session_id(session_key) != new_session_id:
            raise RuntimeError("post-response rebind was not persisted")
        if self.store.peek_session_id(session_key) != new_session_id:
            raise RuntimeError("post-response rebind target verification failed")
        source_payload = payload.get("source")
        if isinstance(source_payload, dict):
            from gateway.session import SessionSource

            self.store._record_gateway_session_peer(
                new_session_id,
                session_key,
                SessionSource.from_dict(source_payload),
            )
            peer = self.store._db.get_session(new_session_id)
            if peer is None or str(peer.get("session_key") or "") != session_key:
                raise RuntimeError("post-response peer refresh was not persisted")

    def _apply_append_to_transcript(self, _action: PostResponseAction, payload: dict[str, Any]) -> None:
        session_id = str(payload["session_id"])
        message = dict(payload["message"])
        if bool(payload.get("skip_db", False)):
            raise RuntimeError("durable skip_db transcript action is invalid")
        # The action can be reclaimed after a process dies between the
        # append and action completion.  Ordered post-response work holds
        # the active turn, so an equal terminal row proves this action's
        # effect already landed and avoids a duplicate replay append.
        from gateway.post_response_transcript import terminal_row_matches

        if not terminal_row_matches(self.store, session_id, message):
            self.store.append_to_transcript(session_id, message)
        if not terminal_row_matches(self.store, session_id, message):
            raise RuntimeError("post-response transcript append was not durable")

    def _apply_update_session(self, _action: PostResponseAction, payload: dict[str, Any]) -> None:
        session_key = str(payload["session_key"])
        self.store.update_session(
            session_key,
            last_prompt_tokens=payload.get("last_prompt_tokens"),
            touch_activity=bool(payload.get("touch_activity", True)),
        )
        if payload.get("clear_resume_pending"):
            self.store.clear_resume_pending(session_key)
        durable = self.store.load_durable_routing_entry(session_key)
        if not isinstance(durable, dict):
            raise RuntimeError("post-response session update was not durable")
        if payload.get("last_prompt_tokens") is not None and durable.get(
            "last_prompt_tokens"
        ) != payload.get("last_prompt_tokens"):
            raise RuntimeError("post-response session token update was not durable")
        if payload.get("clear_resume_pending") and durable.get("resume_pending"):
            raise RuntimeError("post-response resume clear was not durable")

    def _apply_clear_restart_failure_count(self, _action: PostResponseAction, payload: dict[str, Any]) -> None:
        runner = self.runner
        if runner is None:
            raise RuntimeError("post-response runner no longer exists")
        runner._clear_restart_failure_count_sync(str(payload["session_key"]))

    def _apply_sync_topic_binding(self, _action: PostResponseAction, payload: dict[str, Any]) -> None:
        from gateway.session import SessionSource

        source = SessionSource.from_dict(dict(payload["source"]))
        entry = self.store.lookup_by_session_key(str(payload["session_key"]))
        if entry is None:
            raise RuntimeError("post-response topic route is missing")
        runner = self.runner
        if runner is None:
            raise RuntimeError("post-response runner no longer exists")
        runner._sync_telegram_topic_binding(
            source, entry, reason="post-response-finalize"
        )
        if runner._is_telegram_topic_lane(source):
            binding = self.store._db.get_telegram_topic_binding(
                chat_id=str(source.chat_id), thread_id=str(source.thread_id)
            )
            if binding is None or str(binding.get("session_id") or "") != entry.session_id:
                raise RuntimeError("post-response topic binding was not persisted")

    def _apply_finalize_turn(self, action: PostResponseAction, payload: dict[str, Any]) -> None:
        # Claim SQL requires delivery or explicit permanent-target abandonment;
        # retain the defensive check for manually injected/corrupt rows.
        if not self.store._db.is_final_delivery_finalizable(action.obligation_id):
            raise RuntimeError("finalize_turn claimed before delivery receipt")
        runner = self.runner
        if runner is None:
            raise RuntimeError("post-response runner no longer exists")
        runner._finalize_post_response_turn(payload)

    def _apply_clear_resume_pending(self, _action: PostResponseAction, payload: dict[str, Any]) -> None:
        self.store.clear_resume_pending(str(payload["session_key"]))

    def _apply_clear_active_turn(self, _action: PostResponseAction, payload: dict[str, Any]) -> None:
        session_key = str(payload["session_key"])
        token = str(payload["token"])
        cleared = self.store.clear_turn_active(session_key, token)
        if not cleared and self.store.peek_active_turn_token(session_key) == token:
            raise RuntimeError("post-response active-turn clear was not persisted")
