"""Retained restart and durable post-response session routing contracts."""

from __future__ import annotations

import json
import sqlite3
import logging

logger = logging.getLogger("gateway.session")
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from gateway.session_lifecycle import _now

if TYPE_CHECKING:
    from gateway.session import SessionEntry


class SessionRetainedMixin:
    def _register_pending_routing_generation(self, generation: int) -> None:
        pending_lock = getattr(self, "_pending_routing_lock", None)
        if pending_lock is None:
            pending_lock = threading.Lock()
            self._pending_routing_lock = pending_lock
        with pending_lock:
            pending = getattr(self, "_pending_routing_generations", None)
            if pending is None:
                pending = set()
                self._pending_routing_generations = pending
            pending.add(generation)

    def _unregister_pending_routing_generation(self, generation: int) -> None:
        pending_lock = getattr(self, "_pending_routing_lock", None)
        if pending_lock is None:
            return
        with pending_lock:
            pending = getattr(self, "_pending_routing_generations", None)
            if pending is not None:
                pending.discard(generation)

    def _has_older_pending_routing_generation(self, generation: int) -> bool:
        pending_lock = getattr(self, "_pending_routing_lock", None)
        if pending_lock is None:
            return False
        with pending_lock:
            pending = getattr(self, "_pending_routing_generations", set())
            return any(candidate < generation for candidate in pending)

    def rebind_session_id(
        self,
        session_key: str,
        expected_session_id: str,
        new_session_id: str,
    ) -> bool:
        """CAS one routing binding and durably refresh its legacy mirror.

        Compression rotates a session ID without changing its routing key.
        Persist that structural one-key transition with one state.db UPSERT,
        rather than holding a write transaction while reconciling every route.
        A stale run cannot overwrite a newer binding because the mutation is a
        compare-and-swap under ``_lock``.
        """
        with self._lock:
            self._ensure_loaded_locked()
            entry = self._entries.get(session_key)
            if entry is None:
                return False
            if entry.session_id == new_session_id:
                return True
            if entry.session_id != expected_session_id:
                return False
            entry.session_id = new_session_id
            data, generation = self._snapshot_routing_locked()
            # Keep the tentative CAS invisible to every other snapshot until
            # state.db accepts it or it is rolled back. Structural writers use
            # the same _lock -> _save_lock order, avoiding lock inversion.
            persisted = self._persist_routing_data(
                data,
                generation,
                reason="rebind_session_id",
                point_key=session_key,
                require_state_db=True,
            )
            if not persisted:
                entry.session_id = expected_session_id
            return persisted

    def mark_resume_pending_batch(
        self,
        session_keys: list[str],
        reason: str = "restart_timeout",
    ) -> list[str]:
        """Atomically mark multiple sessions resumable in one durable write.

        Shutdown may need to preserve many concurrent turns within one short
        deadline.  Per-key point saves can commit only an arbitrary prefix
        before that deadline expires, so this path publishes one complete
        routing snapshot while holding the routing lock.  Explicitly suspended
        sessions remain excluded.

        Returns the ordered unique keys that existed and were marked.
        """
        unique_keys = list(dict.fromkeys(session_keys))
        marked: list[str] = []
        previous: dict[str, tuple[bool, Optional[str], Optional[datetime]]] = {}
        marked_at = _now()

        with self._lock:
            self._ensure_loaded_locked()
            for session_key in unique_keys:
                entry = self._entries.get(session_key)
                if entry is None or entry.suspended:
                    continue
                previous[session_key] = (
                    entry.resume_pending,
                    entry.resume_reason,
                    entry.last_resume_marked_at,
                )
                entry.resume_pending = True
                entry.resume_reason = reason
                entry.last_resume_marked_at = marked_at
                marked.append(session_key)

            if not marked:
                return []

            data, generation = self._snapshot_routing_locked()
            try:
                persisted = self._persist_routing_data(
                    data,
                    generation,
                    reason="mark_resume_pending_batch",
                    require_state_db=True,
                )
                if not persisted:
                    raise RuntimeError("resume-marker batch was not persisted")
            except BaseException:
                for session_key, old_values in previous.items():
                    entry = self._entries.get(session_key)
                    if entry is None:
                        continue
                    (
                        entry.resume_pending,
                        entry.resume_reason,
                        entry.last_resume_marked_at,
                    ) = old_values
                raise

        return marked

    def peek_active_turn_token(self, session_key: str) -> Optional[str]:
        """Return the current active-turn token without mutating ownership."""
        if not session_key:
            return None
        with self._lock:
            self._ensure_loaded_locked()
            entry = self._entries.get(session_key)
            return getattr(entry, "active_turn_token", None) if entry else None

    def load_durable_routing_entry(self, session_key: str) -> Optional[dict[str, Any]]:
        """Read one routing entry from state.db for post-action verification.

        The in-memory index is deliberately insufficient after a durable action:
        a failed/spooled write can leave it looking updated while a restart loses
        the mutation.  This narrow read is only used by the ordered worker.
        """
        if not session_key or not self._routing_db:
            return None
        loader = getattr(self._routing_db, "load_gateway_routing_entries", None)
        if not callable(loader):
            return None
        try:
            encoded = loader(scope=self._routing_scope()).get(session_key)
            value = json.loads(encoded) if encoded else None
            return value if isinstance(value, dict) else None
        except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error):
            return None

    def set_expiry_finalized(
        self, entry: SessionEntry, *, clear_model_override: bool = True
    ) -> None:
        """Mark a session entry expiry-finalized in memory and state.db.

        Single write-path for the expiry watcher (#9006): keeps the durable
        session flag in sync with the primary state.db routing row.  The
        legacy sessions.json mirror is refreshed by structural saves.

        ``clear_model_override=False`` preserves the give-up path's original
        behavior (flag only, no override drop).
        """
        with self._lock:
            entry.expiry_finalized = True
            if clear_model_override:
                # Session finalization is a conversation boundary — drop the
                # persisted /model override too so a later message doesn't
                # rehydrate it after the in-memory override was popped.
                entry.model_override = None
            session_key = entry.session_key
            session_id = entry.session_id
        self._save_entry(session_key)
        db = self._db_for_key(session_key)
        if db:
            setter = getattr(db, "set_expiry_finalized", None)
            if callable(setter):
                try:
                    setter(session_id, True)
                except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
                    logger.debug(
                        "Session DB expiry_finalized write failed for %s: %s",
                        session_id,
                        exc,
                    )
            try:
                # Expiry finalization is a real conversation boundary. Without
                # a durable ``session_reset`` end_reason, later agent cleanup can
                # close the row as ``agent_close``; stale-route recovery treats
                # that as resumable and resurrects the expired full history.
                #
                # promote_to_session_reset is conditional: it only promotes
                # live rows or rows ended with ``agent_close``.  Explicit
                # boundaries (compression, session_reset, new_command, etc.)
                # are preserved — the first writer wins.
                db.promote_to_session_reset(session_id)
            except (OSError, RuntimeError, ValueError, TypeError, sqlite3.Error) as exc:
                logger.debug(
                    "Session DB promote_to_session_reset failed for %s: %s",
                    session_id,
                    exc,
                )

    def suspend_recently_active(self, max_age_seconds: int = 120) -> int:
        """Mark recently-active sessions as resumable after an unexpected exit.

        Called on gateway startup after a crash or fast restart to preserve
        in-flight sessions instead of destroying their conversation history
        (#7536).  Only marks sessions updated within *max_age_seconds* to
        avoid touching long-idle sessions.  Sets ``resume_pending=True`` so
        the next incoming message on the same session_key auto-resumes from
        the existing transcript.

        Entries already flagged ``resume_pending=True`` are skipped.  Entries
        explicitly ``suspended=True`` (from /stop or stuck-loop escalation)
        are also skipped.  Terminal escalation for genuinely stuck sessions
        is still handled by the existing ``.restart_failure_counts`` counter
        (threshold 3), which runs after this method and sets ``suspended=True``.

        Returns the number of sessions marked resumable.
        """
        from datetime import timedelta

        cutoff = _now() - timedelta(seconds=max_age_seconds)
        count = 0
        with self._lock:
            self._ensure_loaded_locked()
            for entry in self._entries.values():
                if entry.resume_pending:
                    continue
                if not entry.suspended and entry.updated_at >= cutoff:
                    entry.resume_pending = True
                    entry.resume_reason = "restart_interrupted"
                    entry.last_resume_marked_at = _now()
                    count += 1
            if count:
                self._save()
        return count
