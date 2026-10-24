"""Atomic final delivery and ordered durable post-response actions."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, TypeVar

logger = logging.getLogger("hermes_state")
T = TypeVar("T")
_FINALIZABLE_DELIVERY = """(d.state='delivered' OR
    (d.state='abandoned' AND d.last_error IN ('chat not found','bot blocked')))"""


@dataclass(frozen=True)
class FinalDeliveryBarrier:
    """Identity returned after durably recording a final response barrier."""

    obligation_id: str
    action_keys: tuple[str, ...]


@dataclass(frozen=True)
class PostResponseAction:
    """One claimed post-response action.

    The operational session identifier and payload deliberately stay out of
    ``repr`` so routine diagnostics cannot disclose them accidentally.
    """

    obligation_id: str
    ordinal: int
    action_key: str
    action_kind: str
    session_key_hash: str
    session_lineage_hash: str
    session_identifier: str = field(repr=False)
    payload: Any = field(repr=False)
    state: str = "pending"
    attempts: int = 0
    owner_pid: Optional[int] = None
    owner_started_at: Optional[int] = None
    next_retry_at: Optional[float] = None
    # Snapshot of ``attempts`` at claim time.  Keeping it separately avoids
    # accidentally treating a later same-owner reclaim as this claim.
    claim_attempt: int = 0


class SessionPostResponseMixin:
    _POST_RESPONSE_MAX_ATTEMPTS = 3

    _POST_RESPONSE_RETRY_BASE_SECONDS = 1.0

    _POST_RESPONSE_RETRY_MAX_SECONDS = 60.0

    _POST_RESPONSE_SCAN_PAGE_SIZE = 100

    _POST_RESPONSE_SCAN_CAP = 2_000

    @staticmethod
    def _post_response_hash(value: str) -> str:
        return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()

    @classmethod
    def _post_response_action_key(
        cls,
        obligation_id: str,
        ordinal: int,
        action_kind: str,
        payload_json: str,
    ) -> str:
        identity = "\x1f".join(
            (str(obligation_id), str(ordinal), action_kind, payload_json)
        )
        return hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()

    @staticmethod
    def _post_response_owner_stamp() -> tuple[int, Optional[int]]:
        pid = os.getpid()
        try:
            from gateway.status import get_process_start_time

            return pid, get_process_start_time(pid)
        except (ImportError, OSError, RuntimeError, ValueError, TypeError, OverflowError):
            return pid, None

    @staticmethod
    def _post_response_owner_alive(pid: Any, started_at: Any) -> bool:
        """Use the delivery-ledger pid/start-time policy without importing it."""
        if not pid:
            return False
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        try:
            from gateway.status import get_process_start_time

            current_started_at = get_process_start_time(pid)
        except (ImportError, OSError, RuntimeError, ValueError, TypeError, OverflowError):
            current_started_at = None
        if current_started_at is None:
            try:
                from gateway.status import _pid_exists

                return bool(_pid_exists(pid))
            except (ImportError, OSError, RuntimeError, ValueError, TypeError, OverflowError):
                # A failed liveness probe must never steal a live action.
                return True
        try:
            from gateway.status import _pid_exists
            # A matching incarnation can already be a zombie awaiting its parent's reap.
            return (started_at is None or int(current_started_at) == int(started_at)) and _pid_exists(pid)
        except (ImportError, OSError, RuntimeError, ValueError, TypeError, OverflowError):
            # Preserve the ledger's conservative behavior for malformed
            # historical start stamps.
            return True

    @staticmethod
    def _post_response_error_token(error: Any) -> Optional[str]:
        """Return a stable diagnostic token without retaining raw failures.

        Action rows can predate reliable lineage payloads.  Partial redaction
        therefore cannot prove that an arbitrary exception is safe to retain.
        """
        text = str(error or "")
        if not text:
            return None
        return "sha256:" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()

    @staticmethod
    def _post_response_action_from_row(row: sqlite3.Row) -> PostResponseAction:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
        return PostResponseAction(
            obligation_id=str(row["obligation_id"]),
            ordinal=int(row["ordinal"]),
            action_key=str(row["action_key"]),
            action_kind=str(row["action_kind"]),
            session_key_hash=str(row["session_key_hash"]),
            session_lineage_hash=str(row["session_lineage_hash"]),
            session_identifier=str(row["session_identifier_payload"]),
            payload=payload,
            state=str(row["state"]),
            attempts=int(row["attempts"]),
            owner_pid=row["owner_pid"],
            owner_started_at=row["owner_started_at"],
            next_retry_at=row["next_retry_at"],
            claim_attempt=int(row["attempts"]),
        )

    def create_final_delivery_and_barrier(
        self,
        *,
        obligation_id: str,
        session_key: str,
        platform: str,
        chat_id: str,
        thread_id: Optional[str],
        content: str,
        actions: list[dict[str, Any]],
        session_lineage: Optional[str] = None,
        adapter_profile: Optional[str] = None,
    ) -> FinalDeliveryBarrier:
        """Atomically persist a final-delivery receipt and ordered actions.

        Identities are deterministic and inserts never replace an existing
        row. Retrying after a crash therefore cannot reset attempts, state,
        or ownership that a worker has already advanced.
        """
        from agent.redact import redact_sensitive_text

        content = redact_sensitive_text(content)
        lineage = str(session_lineage if session_lineage is not None else session_key)
        session_identifier = str(session_key)
        action_rows = []
        for ordinal, action in enumerate(actions):
            if not isinstance(action, dict):
                raise ValueError("post-response action must be a mapping")
            action_kind = str(action.get("action_kind", action.get("kind", "")))
            if not action_kind:
                raise ValueError("post-response action kind must not be empty")
            payload_json = json.dumps(
                action.get("payload", {}),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            action_rows.append(
                (
                    ordinal,
                    self._post_response_action_key(
                        obligation_id, ordinal, action_kind, payload_json
                    ),
                    action_kind,
                    payload_json,
                )
            )
        now = time.time()
        session_key_hash = self._post_response_hash(session_identifier)
        lineage_hash = self._post_response_hash(lineage)
        owner_pid, owner_started_at = self._post_response_owner_stamp()

        def _do(conn):
            # Validate a retry as a complete immutable receipt, not merely an
            # identity hit. Returning success for a changed final response
            # would falsely tell the caller that its barrier is durable.
            existing = conn.execute(
                """SELECT session_key, platform, chat_id, thread_id, content, adapter_profile
                   FROM delivery_obligations WHERE obligation_id=?""",
                (obligation_id,),
            ).fetchone()
            expected_obligation = (
                session_identifier,
                str(platform),
                str(chat_id),
                str(thread_id) if thread_id is not None else None,
                str(content),
                adapter_profile,
            )
            if existing is not None:
                if tuple(existing) != expected_obligation:
                    raise ValueError(
                        "existing delivery obligation does not match retry"
                    )
                actual_actions = conn.execute(
                    """SELECT ordinal, action_key, action_kind, payload_json,
                              session_key_hash, session_lineage_hash,
                              session_identifier_payload, session_lineage_payload
                       FROM gateway_post_response_actions
                       WHERE obligation_id=? ORDER BY ordinal""",
                    (obligation_id,),
                ).fetchall()
                expected_actions = [
                    (
                        ordinal,
                        action_key,
                        action_kind,
                        payload_json,
                        session_key_hash,
                        lineage_hash,
                        session_identifier,
                        lineage,
                    )
                    for ordinal, action_key, action_kind, payload_json in action_rows
                ]
                if [tuple(row) for row in actual_actions] != expected_actions:
                    raise ValueError(
                        "existing post-response action set does not match retry"
                    )
                return FinalDeliveryBarrier(
                    obligation_id=str(obligation_id),
                    action_keys=tuple(row[1] for row in action_rows),
                )

            action_keys = set()
            for _ordinal, action_key, _action_kind, _payload_json in action_rows:
                if (
                    action_key in action_keys
                    or conn.execute(
                        "SELECT 1 FROM gateway_post_response_actions WHERE action_key=?",
                        (action_key,),
                    ).fetchone()
                    is not None
                ):
                    raise ValueError("post-response action identity collision")
                action_keys.add(action_key)

            conn.execute(
                """INSERT INTO delivery_obligations
                   (obligation_id, session_key, platform, chat_id, thread_id,
                    content, state, attempts, created_at, updated_at,
                    owner_pid, owner_started_at, adapter_profile)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?)""",
                (
                    obligation_id,
                    session_identifier,
                    platform,
                    str(chat_id),
                    str(thread_id) if thread_id is not None else None,
                    content,
                    now,
                    now,
                    owner_pid,
                    owner_started_at,
                    adapter_profile,
                ),
            )
            for ordinal, action_key, action_kind, payload_json in action_rows:
                conn.execute(
                    """INSERT INTO gateway_post_response_actions
                       (obligation_id, ordinal, action_key, action_kind,
                        session_key_hash, session_lineage_hash, session_identifier_payload,
                        session_lineage_payload, payload_json, state, attempts,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)""",
                    (
                        obligation_id,
                        ordinal,
                        action_key,
                        action_kind,
                        session_key_hash,
                        lineage_hash,
                        session_identifier,
                        lineage,
                        payload_json,
                        now,
                        now,
                    ),
                )
            return FinalDeliveryBarrier(
                obligation_id=str(obligation_id),
                action_keys=tuple(row[1] for row in action_rows),
            )

        # One callback means exactly one BEGIN IMMEDIATE/commit pair.
        return self._execute_write(
            _do,
            operation="create_final_delivery_and_barrier",
            items=len(action_rows) + 1,
        )

    def has_post_response_barrier(
        self, session_key: str, session_lineage: Optional[str] = None
    ) -> bool:
        lineage = str(session_lineage if session_lineage is not None else session_key)
        session_key_hash = self._post_response_hash(str(session_key))
        lineage_hash = self._post_response_hash(lineage)
        with self._read_ctx() as conn:
            row = conn.execute(
                """SELECT 1 FROM gateway_post_response_actions
                   WHERE (session_key_hash = ? OR session_lineage_hash = ?)
                     AND state != 'done' LIMIT 1""",
                (session_key_hash, lineage_hash),
            ).fetchone()
        return row is not None

    def mark_final_delivery_attempting(self, obligation_id: str) -> bool:
        """Prove the exact precommitted obligation crossed the send boundary."""

        def _do(conn):
            cursor = conn.execute(
                """UPDATE delivery_obligations SET state='attempting', updated_at=?,
                   attempts=attempts+1, last_error=NULL WHERE obligation_id=?
                   AND state='pending'""",
                (time.time(), str(obligation_id)),
            )
            return cursor.rowcount == 1

        return bool(
            self._execute_write(_do, operation="mark_final_delivery_attempting")
        )

    def post_response_finalization_ready(self, session_key: str, session_lineage: str) -> bool:
        """Whether every unfinished barrier has a terminal delivery outcome."""
        with self._read_ctx() as conn:
            row = conn.execute(
                """SELECT 1 FROM gateway_post_response_actions AS a
                   JOIN delivery_obligations AS d ON d.obligation_id=a.obligation_id
                   WHERE (a.session_key_hash=? OR a.session_lineage_hash=?)
                     AND a.state != 'done' AND NOT """ + _FINALIZABLE_DELIVERY + " LIMIT 1",
                (self._post_response_hash(session_key), self._post_response_hash(session_lineage)),
            ).fetchone()
        return row is None

    def settle_final_delivery(
        self, obligation_id: str, *, delivered: bool, error: str = "",
        operational_error: Optional[str] = None, expected_attempts: Optional[int] = None,
    ) -> bool:
        """Record final platform delivery outcome on this SessionDB receipt."""
        token = "sha256:" + hashlib.sha256(str(error or "").encode("utf-8", "replace")).hexdigest()
        if operational_error in {"send_path_degraded", "chat not found", "bot blocked"}:
            token = operational_error
        elif str(operational_error or "").startswith("flood_control:"):
            try:
                delay = float(operational_error.split(":", 1)[1])
            except (ValueError, TypeError):
                delay = math.nan
            if math.isfinite(delay) and 0 < delay <= 900:
                token = f"flood_control:{delay:g}"
        terminal_state = "abandoned" if token in {"chat not found", "bot blocked"} else "failed"
        owner_pid, owner_started_at = self._post_response_owner_stamp()

        def _do(conn):
            guard = "" if expected_attempts is None else " AND attempts=?"
            params = (
                "delivered" if delivered else terminal_state, time.time(),
                None if delivered else token, str(obligation_id), owner_pid, owner_started_at,
            )
            if expected_attempts is not None:
                params += (expected_attempts,)
            cursor = conn.execute(
                """UPDATE delivery_obligations SET state=?, updated_at=?, last_error=?
                   WHERE obligation_id=? AND state='attempting'
                     AND owner_pid IS ? AND owner_started_at IS ?""" + guard,
                params,
            )
            return cursor.rowcount == 1

        return bool(self._execute_write(_do, operation="settle_final_delivery"))

    def is_final_delivery_delivered(self, obligation_id: str) -> bool:
        """Return whether a final-delivery receipt is confirmed delivered.

        ``failed`` is deliberately not final for post-response finalization:
        the delivery ledger retries it on startup, while the active-turn
        barrier prevents a later turn from overtaking the owed response.
        ``abandoned`` likewise remains fail-closed; the ledger retains that
        receipt while its barrier exists so the retained active token has a
        durable, inspectable explanation.
        """
        with self._read_ctx() as conn:
            row = conn.execute(
                "SELECT state FROM delivery_obligations WHERE obligation_id=?",
                (str(obligation_id),),
            ).fetchone()
        return row is not None and row["state"] == "delivered"

    def is_final_delivery_finalizable(self, obligation_id: str) -> bool:
        """Delivered, or explicitly refused by a permanently unavailable target."""
        with self._read_ctx() as conn:
            return conn.execute(
                "SELECT 1 FROM delivery_obligations AS d WHERE obligation_id=? AND "
                + _FINALIZABLE_DELIVERY, (str(obligation_id),),
            ).fetchone() is not None

    def list_ready_post_response_actions(
        self, *, limit: int = 20, now: Optional[float] = None
    ) -> list[PostResponseAction]:
        """Return a bounded, non-mutating view of actions eligible to claim."""
        now = time.time() if now is None else float(now)
        limit = max(0, min(int(limit), 100))
        if not limit:
            return []

        def _candidate_page(conn, cursor, page_size):
            cursor_sql = ""
            params: list[Any] = [now]
            if cursor is not None:
                priority, obligation_id, ordinal = cursor
                cursor_sql = """
                     AND (CASE WHEN a.state IN ('pending', 'failed') THEN 0 ELSE 1 END > ?
                          OR (CASE WHEN a.state IN ('pending', 'failed') THEN 0 ELSE 1 END = ?
                              AND (a.obligation_id > ?
                                   OR (a.obligation_id = ? AND a.ordinal > ?))))"""
                params.extend(
                    (priority, priority, obligation_id, obligation_id, ordinal)
                )
            params.append(page_size)
            return conn.execute(
                """SELECT a.* FROM gateway_post_response_actions AS a
                   WHERE NOT EXISTS (
                       SELECT 1 FROM gateway_post_response_actions AS earlier
                       WHERE earlier.obligation_id = a.obligation_id
                         AND earlier.ordinal < a.ordinal AND earlier.state != 'done'
                   )
                     -- Delivery must finish, or a definitive dead target must be
                     -- abandoned, before finalization releases the admission barrier.
                     -- Failed/attempting/pending receipts remain eligible for
                     -- ledger recovery, while earlier non-final actions can
                     -- still drain in ordinal order.
                     AND (a.action_kind != 'finalize_turn' OR EXISTS (
                          SELECT 1 FROM delivery_obligations AS d
                          WHERE d.obligation_id=a.obligation_id
                            AND """ + _FINALIZABLE_DELIVERY + """
                     ))
                     AND (a.state = 'running'
                          OR (a.state IN ('pending', 'failed')
                              AND (a.next_retry_at IS NULL OR a.next_retry_at <= ?)))
                   -- Keep live running reclaim candidates from consuming the
                   -- scan before due fresh/retry work. The NOT EXISTS predicate
                   -- above continues to enforce ordinal barriers.
                   """
                + cursor_sql
                + """
                   ORDER BY CASE WHEN a.state IN ('pending', 'failed') THEN 0 ELSE 1 END,
                            a.obligation_id, a.ordinal
                   LIMIT ?""",
                params,
            ).fetchall()

        ready = []
        scanned = 0
        scan_cursor = None
        with self._read_ctx() as conn:
            while scanned < self._POST_RESPONSE_SCAN_CAP and len(ready) < limit:
                page_size = min(
                    self._POST_RESPONSE_SCAN_PAGE_SIZE,
                    self._POST_RESPONSE_SCAN_CAP - scanned,
                )
                rows = _candidate_page(conn, scan_cursor, page_size)
                if not rows:
                    break
                scanned += len(rows)
                for row in rows:
                    scan_cursor = (
                        0 if row["state"] in {"pending", "failed"} else 1,
                        str(row["obligation_id"]),
                        int(row["ordinal"]),
                    )
                    if row["state"] == "running" and self._post_response_owner_alive(
                        row["owner_pid"], row["owner_started_at"]
                    ):
                        continue
                    ready.append(self._post_response_action_from_row(row))
                    if len(ready) >= limit:
                        break
                if len(rows) < page_size:
                    break
        if scanned >= self._POST_RESPONSE_SCAN_CAP and len(ready) < limit:
            logger.warning(
                "post-response ready scan saturated bounded cap (%d)",
                self._POST_RESPONSE_SCAN_CAP,
            )
        return ready

    def claim_ready_post_response_actions(
        self,
        *,
        limit: int = 20,
        now: Optional[float] = None,
        max_attempts: int = _POST_RESPONSE_MAX_ATTEMPTS,
    ) -> list[PostResponseAction]:
        """Claim due ordered actions, reclaiming only dead/stale owners."""
        now = time.time() if now is None else float(now)
        limit = max(0, min(int(limit), 100))
        if not limit:
            return []
        max_attempts = max(1, int(max_attempts))
        owner_pid, owner_started_at = self._post_response_owner_stamp()

        def _do(conn):
            def _candidate_page(cursor, page_size):
                cursor_sql = ""
                params: list[Any] = [now]
                if cursor is not None:
                    priority, obligation_id, ordinal = cursor
                    cursor_sql = """
                         AND (CASE WHEN a.state IN ('pending', 'failed') THEN 0 ELSE 1 END > ?
                              OR (CASE WHEN a.state IN ('pending', 'failed') THEN 0 ELSE 1 END = ?
                                  AND (a.obligation_id > ?
                                       OR (a.obligation_id = ? AND a.ordinal > ?))))"""
                    params.extend(
                        (priority, priority, obligation_id, obligation_id, ordinal)
                    )
                params.append(page_size)
                return conn.execute(
                    """SELECT a.* FROM gateway_post_response_actions AS a
                   WHERE NOT EXISTS (
                       SELECT 1 FROM gateway_post_response_actions AS earlier
                       WHERE earlier.obligation_id = a.obligation_id
                         AND earlier.ordinal < a.ordinal AND earlier.state != 'done'
                   )
                     -- Keep the claim predicate identical to the readiness
                     -- predicate: only a positive receipt may finalize.
                     AND (a.action_kind != 'finalize_turn' OR EXISTS (
                          SELECT 1 FROM delivery_obligations AS d
                          WHERE d.obligation_id=a.obligation_id
                            AND """ + _FINALIZABLE_DELIVERY + """
                     ))
                     AND (a.state = 'running'
                          OR (a.state IN ('pending', 'failed')
                              AND (a.next_retry_at IS NULL OR a.next_retry_at <= ?)))
                   -- Prefer due fresh/retry work before reclaim candidates
                   -- without weakening ordinal barriers.
                   """
                    + cursor_sql
                    + """
                   ORDER BY CASE WHEN a.state IN ('pending', 'failed') THEN 0 ELSE 1 END,
                            a.obligation_id, a.ordinal
                   LIMIT ?""",
                    params,
                ).fetchall()

            claimed = []
            scanned = 0
            scan_cursor = None
            while scanned < self._POST_RESPONSE_SCAN_CAP and len(claimed) < limit:
                page_size = min(
                    self._POST_RESPONSE_SCAN_PAGE_SIZE,
                    self._POST_RESPONSE_SCAN_CAP - scanned,
                )
                candidates = _candidate_page(scan_cursor, page_size)
                if not candidates:
                    break
                scanned += len(candidates)
                for row in candidates:
                    scan_cursor = (
                        0 if row["state"] in {"pending", "failed"} else 1,
                        str(row["obligation_id"]),
                        int(row["ordinal"]),
                    )
                    state = str(row["state"])
                    prior_pid = row["owner_pid"]
                    prior_started_at = row["owner_started_at"]
                    if state == "running" and self._post_response_owner_alive(
                        prior_pid, prior_started_at
                    ):
                        continue
                    if int(row["attempts"]) >= max_attempts:
                        conn.execute(
                            """UPDATE gateway_post_response_actions
                               SET state='blocked', owner_pid=NULL, owner_started_at=NULL,
                                   next_retry_at=NULL, updated_at=?
                               WHERE action_key=? AND state=? AND owner_pid IS ?
                                 AND owner_started_at IS ?""",
                            (
                                now,
                                row["action_key"],
                                state,
                                prior_pid,
                                prior_started_at,
                            ),
                        )
                        continue
                    update = conn.execute(
                        """UPDATE gateway_post_response_actions
                           SET state='running', owner_pid=?, owner_started_at=?,
                               attempts=attempts+1, next_retry_at=NULL, last_error=NULL,
                               updated_at=?
                           WHERE action_key=? AND state=? AND owner_pid IS ?
                             AND owner_started_at IS ?""",
                        (
                            owner_pid,
                            owner_started_at,
                            now,
                            row["action_key"],
                            state,
                            prior_pid,
                            prior_started_at,
                        ),
                    )
                    if update.rowcount:
                        fresh = conn.execute(
                            "SELECT * FROM gateway_post_response_actions WHERE action_key=?",
                            (row["action_key"],),
                        ).fetchone()
                        claimed.append(self._post_response_action_from_row(fresh))
                        if len(claimed) >= limit:
                            break
                if len(candidates) < page_size:
                    break
            if scanned >= self._POST_RESPONSE_SCAN_CAP and len(claimed) < limit:
                logger.warning(
                    "post-response claim scan saturated bounded cap (%d)",
                    self._POST_RESPONSE_SCAN_CAP,
                )
            return claimed

        return self._execute_write(
            _do, operation="claim_ready_post_response_actions", items=limit
        )

    def complete_post_response_action(
        self,
        action_key: str,
        *,
        claimed_attempt: int,
        owner_pid: Optional[int] = None,
        owner_started_at: Optional[int] = None,
        now: Optional[float] = None,
    ) -> bool:
        if owner_pid is None:
            owner_pid, owner_started_at = self._post_response_owner_stamp()
        now = time.time() if now is None else float(now)

        def _do(conn):
            cursor = conn.execute(
                """UPDATE gateway_post_response_actions
                   SET state='done', owner_pid=NULL, owner_started_at=NULL,
                       next_retry_at=NULL, last_error=NULL, updated_at=?
                   WHERE action_key=? AND state='running' AND owner_pid IS ?
                     AND owner_started_at IS ? AND attempts=?""",
                (now, action_key, owner_pid, owner_started_at, int(claimed_attempt)),
            )
            return cursor.rowcount == 1

        return bool(self._execute_write(_do, operation="complete_post_response_action"))

    def fail_post_response_action(
        self,
        action_key: str,
        *,
        claimed_attempt: int,
        owner_pid: Optional[int] = None,
        owner_started_at: Optional[int] = None,
        error: Any = "",
        now: Optional[float] = None,
        max_attempts: int = _POST_RESPONSE_MAX_ATTEMPTS,
    ) -> bool:
        if owner_pid is None:
            owner_pid, owner_started_at = self._post_response_owner_stamp()
        now = time.time() if now is None else float(now)
        max_attempts = max(1, int(max_attempts))

        def _do(conn):
            row = conn.execute(
                """SELECT attempts, session_identifier_payload, session_lineage_payload
                   FROM gateway_post_response_actions
                   WHERE action_key=? AND state='running' AND owner_pid IS ?
                     AND owner_started_at IS ? AND attempts=?""",
                (action_key, owner_pid, owner_started_at, int(claimed_attempt)),
            ).fetchone()
            if row is None:
                return False
            attempts = int(row["attempts"])
            terminal = attempts >= max_attempts
            delay = None
            if not terminal:
                # Legacy/corrupt rows can carry arbitrarily large attempt counts.
                # Clamp before exponentiation: waiting past the capped retry delay
                # has no meaning, and overflowing here would strand the owned row
                # in ``running``.
                retry_exponent = min(max(0, attempts - 1), 60)
                delay = min(
                    self._POST_RESPONSE_RETRY_MAX_SECONDS,
                    self._POST_RESPONSE_RETRY_BASE_SECONDS * (2**retry_exponent),
                )
            cursor = conn.execute(
                """UPDATE gateway_post_response_actions
                   SET state=?, owner_pid=NULL, owner_started_at=NULL,
                       next_retry_at=?, last_error=?, updated_at=?
                   WHERE action_key=? AND state='running' AND owner_pid IS ?
                     AND owner_started_at IS ? AND attempts=?""",
                (
                    "blocked" if terminal else "failed",
                    None if terminal else now + delay,
                    self._post_response_error_token(error),
                    now,
                    action_key,
                    owner_pid,
                    owner_started_at,
                    int(claimed_attempt),
                ),
            )
            return cursor.rowcount == 1

        return bool(self._execute_write(_do, operation="fail_post_response_action"))

    def get_post_response_barrier_diagnostics(
        self, obligation_id: str
    ) -> list[dict[str, Any]]:
        """Return operational status without session identifiers or payloads."""
        with self._read_ctx() as conn:
            rows = conn.execute(
                """SELECT obligation_id, ordinal, action_kind, state, attempts, last_error
                   FROM gateway_post_response_actions
                   WHERE obligation_id=? ORDER BY ordinal""",
                (str(obligation_id),),
            ).fetchall()
        return [dict(row) for row in rows]
