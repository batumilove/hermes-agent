"""Retained fork session and routing contracts on the upstream domain APIs."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, TypeVar

logger = logging.getLogger("hermes_state")
T = TypeVar("T")


class SessionRetainedMixin:
    def replace_gateway_routing_entries(
        self,
        entries: dict[str, str],
        *,
        scope: str = "",
        reason: str = "unspecified",
    ) -> None:
        """Atomically reconcile the routing index for *scope* with *entries*.

        Keys absent from *entries* are removed, changed/new entries are
        upserted, and identical rows retain their existing ``updated_at``.
        Retired sessions cannot be resurrected by a stale routing mirror.
        Other scopes are untouched. ``reason`` is a bounded, content-free
        routing call-site label used only for latency attribution.
        """
        now = time.time()
        desired = {key: value for key, value in entries.items() if key and value}

        def _do(conn):
            existing = {
                row["session_key"]: row["entry_json"]
                for row in conn.execute(
                    "SELECT session_key, entry_json FROM gateway_routing "
                    "WHERE scope = ?",
                    (scope,),
                )
            }
            stale_keys = existing.keys() - desired.keys()
            if stale_keys:
                conn.executemany(
                    "DELETE FROM gateway_routing WHERE scope = ? AND session_key = ?",
                    [(scope, key) for key in stale_keys],
                )
            changed = [
                (scope, key, value, now, value)
                for key, value in desired.items()
                if existing.get(key) != value
            ]
            if changed:
                conn.executemany(
                    "INSERT INTO gateway_routing "
                    "(scope, session_key, entry_json, updated_at) "
                    "SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM state_meta "
                    "WHERE key='gateway.retired_session.v1.' || json_extract(?, '$.session_id')) "
                    "ON CONFLICT(scope, session_key) DO UPDATE SET "
                    "entry_json = excluded.entry_json, updated_at = excluded.updated_at",
                    changed,
                )

        reason_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(reason or "unspecified"))[
            :64
        ]
        self._execute_write(
            _do,
            operation="replace_gateway_routing_entries",
            items=len(entries),
            reason=reason_label,
        )

    def delete_gateway_routing_entries(
        self,
        session_keys: list[str],
        *,
        scope: str = "",
        reason: str = "unspecified",
    ) -> None:
        """Remove routing entries for the given session keys in *scope*."""
        if not session_keys:
            return

        def _do(conn):
            conn.executemany(
                "DELETE FROM gateway_routing WHERE scope = ? AND session_key = ?",
                [(scope, k) for k in session_keys],
            )

        reason_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(reason or "unspecified"))[
            :64
        ]
        self._execute_write(
            _do,
            operation="delete_gateway_routing_entries",
            items=len(session_keys),
            reason=reason_label,
        )

    def list_gateway_routing_origins(
        self,
        *,
        platform: Optional[str] = None,
        active_only: bool = True,
    ) -> list[dict[str, Any]]:
        """List only routing fields from the newest gateway session per key.

        Channel-directory discovery does not need prompts, messages, token
        totals, activity timestamps, or ordering.  Keep this projection narrow
        so periodic discovery does not pay the cost of a full session listing.
        Gateway platform names are normalized lowercase before exact matching so
        SQLite can use the source index without wrapping the column in LOWER().
        """
        query = """
            SELECT origin_json, chat_id, thread_id, display_name, chat_type
            FROM sessions
            WHERE (session_key, started_at) IN (
                SELECT session_key, MAX(started_at)
                FROM sessions
                WHERE session_key IS NOT NULL
                GROUP BY session_key
            )
        """
        params: list = []
        if platform:
            query += " AND source = ?"
            params.append(platform.lower())
        if active_only:
            query += " AND ended_at IS NULL"
        with self._read_ctx() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_session_activity(self, session_id: str) -> Optional[dict[str, Any]]:
        """Return the durable activity snapshot for *session_id*, or None."""
        if not session_id:
            return None
        row = self.get_session(session_id)
        if not row:
            return None
        from agent.session_activity import build_activity_snapshot

        return build_activity_snapshot(
            last_activity_at=row.get("last_activity_at"),
            last_activity_description=row.get("last_activity_description"),
            last_activity_provenance=row.get("last_activity_provenance"),
        )

    def get_dominant_session_model_route(
        self, session_id: str
    ) -> Optional[dict[str, Any]]:
        """Return the main-loop model route that served most API calls.

        ``sessions`` is a legacy aggregate row and can hold model/provider fields
        written by different route changes. ``session_model_usage`` keeps the
        coherent per-call tuple, so persisted status and billing reads should use
        its dominant main-loop route when one is available.
        """
        self.flush_token_counts()
        with self._read_ctx() as conn:
            row = conn.execute(
                """SELECT model, billing_provider, billing_base_url, billing_mode,
                          api_call_count
                     FROM session_model_usage
                    WHERE session_id = ?
                      AND task = ''
                      AND model <> 'unknown'
                      AND billing_provider <> ''
                    ORDER BY api_call_count DESC,
                             (input_tokens + output_tokens + cache_read_tokens +
                              cache_write_tokens + reasoning_tokens) DESC,
                             last_seen DESC
                    LIMIT 1""",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def set_auto_title_if_empty(self, session_id: str, title: str) -> bool:
        """Back-compat shim: set an LLM title only if nothing better exists.

        Retained because older callers (and third-party plugins) reference it
        by name. New code should call :meth:`set_auto_title` with an explicit
        source.
        """
        return self.set_auto_title(session_id, title, source=self.TITLE_SOURCE_LLM)

    def latest_user_message_row_id(self, session_id: str) -> Optional[int]:
        """Row id of the most recent active user message, or ``None``.

        The agent's default reaction target: "the message that triggered me",
        so the model never has to thread row ids through a tool call (mirrors
        the photon adapter's ``_record_last_inbound``).
        """
        return self.latest_message_row_id(session_id, role="user")

    def restore_rewound(self, session_id: str, since_message_id: int) -> int:
        """Mark inactive messages with id >= *since_message_id* active again.

        Returns the number of rows flipped back to ``active=1``.
        Intended for undo-of-rewind and test cleanup; not wired to a
        slash command in v1.
        """

        def _do(conn):
            cursor = conn.execute(
                "SELECT id FROM messages "
                "WHERE session_id = ? AND id >= ? AND active = 0",
                (session_id, since_message_id),
            )
            ids = [r[0] for r in cursor.fetchall()]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(
                    f"UPDATE messages SET active = 1 WHERE id IN ({placeholders})",
                    ids,
                )
            return len(ids)

        return self._execute_write(_do)
