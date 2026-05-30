#!/usr/bin/env python3
"""Backfill Hermes SQLite sessions into self-hosted Honcho.

Default: upload one transcript file per Hermes session into the matching Honcho
session. This avoids rewriting timestamps as current chat messages and lets the
job resume safely using a local state file.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

# Make repo imports work when run from ~/.hermes/scripts.
REPO = Path.home() / ".hermes" / "hermes-agent"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from plugins.memory.honcho.client import HonchoClientConfig, get_honcho_client, reset_honcho_client  # noqa: E402
from plugins.memory.honcho.session import HonchoSessionManager  # noqa: E402

HERMES_HOME = Path.home() / ".hermes"
STATE_DB = HERMES_HOME / "state.db"
PROGRESS_PATH = HERMES_HOME / "state" / "honcho_backfill_progress.json"
LOG_PATH = HERMES_HOME / "logs" / "honcho_backfill.log"

ROLE_MAP = {
    "user": "user",
    "assistant": "assistant",
    "tool": "tool",
    "system": "system",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_progress() -> dict:
    if PROGRESS_PATH.exists():
        try:
            return json.loads(PROGRESS_PATH.read_text())
        except Exception:
            pass
    return {"uploaded": {}, "failed": {}, "started_at": now_iso()}


def save_progress(progress: dict) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(progress, indent=2, sort_keys=True))
    tmp.replace(PROGRESS_PATH)


def log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{now_iso()} {line}\n")


def sanitize_id(id_str: str) -> str:
    # Match HonchoSessionManager._sanitize_id without importing private method.
    import re
    return re.sub(r"[^a-zA-Z0-9_-]", "-", id_str)


def session_key(row: sqlite3.Row) -> str:
    # Preserve the same key shape Hermes uses today for directory-based CLI sessions
    # where possible, while making old sessions addressable by original id.
    return row["id"]


def honcho_session_id_for(row: sqlite3.Row) -> str:
    return sanitize_id(session_key(row))


def peer_ids(cfg: HonchoClientConfig, row: sqlite3.Row) -> tuple[str, str]:
    # Single-user deployment: use configured peerName for the human so old CLI,
    # Telegram, and cron sessions all enrich the same user model. Cron/tool-heavy
    # sessions are still source-labeled in transcript metadata.
    user_peer = cfg.peer_name or row["user_id"] or "user"
    assistant_peer = cfg.ai_peer or "hermes"
    return sanitize_id(str(user_peer)), sanitize_id(str(assistant_peer))


def fetch_sessions(conn: sqlite3.Connection, sources: set[str] | None) -> list[sqlite3.Row]:
    if sources:
        placeholders = ",".join("?" for _ in sources)
        return conn.execute(
            f"""
            SELECT s.*, COALESCE(MAX(m.timestamp), s.started_at) AS last_active
            FROM sessions s
            JOIN messages m ON m.session_id = s.id
            WHERE s.source IN ({placeholders})
            GROUP BY s.id
            HAVING COUNT(m.id) > 0
            ORDER BY s.started_at ASC
            """,
            tuple(sorted(sources)),
        ).fetchall()
    return conn.execute(
        """
        SELECT s.*, COALESCE(MAX(m.timestamp), s.started_at) AS last_active
        FROM sessions s
        JOIN messages m ON m.session_id = s.id
        GROUP BY s.id
        HAVING COUNT(m.id) > 0
        ORDER BY s.started_at ASC
        """
    ).fetchall()


def fetch_messages(conn: sqlite3.Connection, session_id: str, include_tools: bool) -> list[sqlite3.Row]:
    roles = ("user", "assistant", "system", "tool") if include_tools else ("user", "assistant", "system")
    placeholders = ",".join("?" for _ in roles)
    return conn.execute(
        f"""
        SELECT id, role, content, tool_name, tool_calls, timestamp, reasoning, reasoning_content
        FROM messages
        WHERE session_id = ? AND role IN ({placeholders})
        ORDER BY id ASC
        """,
        (session_id, *roles),
    ).fetchall()


def clean_content(msg: sqlite3.Row, include_tools: bool) -> str:
    content = msg["content"] or ""
    if msg["role"] == "tool" and include_tools:
        prefix = f"[tool:{msg['tool_name'] or 'unknown'}] "
        content = prefix + content
    return content.strip()


def format_transcript(row: sqlite3.Row, messages: list[sqlite3.Row], include_tools: bool) -> bytes:
    title = row["title"] or ""
    source = row["source"] or "unknown"
    started = row["started_at"]
    ended = row["ended_at"]
    parent = row["parent_session_id"] or ""
    header = [
        "<hermes_past_session>",
        "<context>",
        "This is a historical Hermes session imported into Honcho after Honcho was enabled.",
        "Use it as background memory. It may include task progress, decisions, preferences,",
        "environment facts, and assistant actions from before the Honcho integration existed.",
        "</context>",
        f'<session id="{row["id"]}" source="{source}" title="{title}" parent="{parent}">',
        f"started_at={started}",
        f"ended_at={ended}",
        f"message_count={len(messages)}",
        f"include_tools={include_tools}",
        "<transcript>",
    ]
    lines = header
    for msg in messages:
        content = clean_content(msg, include_tools)
        if not content:
            continue
        ts = msg["timestamp"] or ""
        role = ROLE_MAP.get(msg["role"], msg["role"] or "unknown")
        # Bound very large tool/messages to keep one upload manageable while preserving signal.
        if len(content) > 20000:
            content = content[:20000] + "\n[...truncated during Honcho backfill...]"
        lines.append(f"[{ts}] {role}: {content}")
    lines.extend(["</transcript>", "</session>", "</hermes_past_session>"])
    return "\n".join(lines).encode("utf-8", errors="replace")


def upload_session(client, cfg: HonchoClientConfig, row: sqlite3.Row, transcript: bytes, msg_count: int) -> None:
    user_peer_id, ai_peer_id = peer_ids(cfg, row)
    user_peer = client.peer(user_peer_id)
    ai_peer = client.peer(ai_peer_id)
    hsession_id = honcho_session_id_for(row)
    hsession = client.session(hsession_id)
    # Configure peers/observation the same way as the Hermes manager.
    try:
        from honcho.session import SessionPeerConfig
        hsession.add_peers([
            (user_peer, SessionPeerConfig(observe_me=cfg.user_observe_me, observe_others=cfg.user_observe_others)),
            (ai_peer, SessionPeerConfig(observe_me=cfg.ai_observe_me, observe_others=cfg.ai_observe_others)),
        ])
    except Exception:
        # Older SDK/server can still accept upload_file without explicit config.
        pass

    metadata = {
        "source": "hermes_state_backfill",
        "hermes_session_id": row["id"],
        "hermes_source": row["source"],
        "title": row["title"] or "",
        "message_count": msg_count,
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
    }
    # Stable unique-ish filename for human inspection. Progress file prevents repeats.
    filename = f"hermes-session-{row['id']}.txt"
    hsession.upload_file(
        file=(filename, transcript, "text/plain"),
        peer=user_peer,
        metadata=metadata,
        created_at=row["started_at"],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default="", help="Comma-separated source filter, e.g. telegram,cli,cron. Empty means all.")
    ap.add_argument("--include-tools", action="store_true", help="Include tool messages as transcript lines. Default keeps user/assistant/system only.")
    ap.add_argument("--limit", type=int, default=0, help="Max sessions this run; 0 means all remaining.")
    ap.add_argument("--sleep", type=float, default=0.05, help="Pause between uploads.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sources = {s.strip() for s in args.sources.split(",") if s.strip()} or None
    progress = load_progress()
    uploaded = progress.setdefault("uploaded", {})
    failed = progress.setdefault("failed", {})

    reset_honcho_client()
    cfg = HonchoClientConfig.from_global_config()
    if not cfg.enabled or not (cfg.base_url or cfg.api_key):
        raise SystemExit("Honcho is not configured")
    client = get_honcho_client(cfg)

    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    sessions = fetch_sessions(conn, sources)
    remaining = [s for s in sessions if s["id"] not in uploaded]
    if args.limit:
        remaining = remaining[: args.limit]

    print(json.dumps({
        "db": str(STATE_DB),
        "total_sessions_matching": len(sessions),
        "already_uploaded": len(uploaded),
        "remaining_this_run": len(remaining),
        "sources": sorted(sources) if sources else "all",
        "include_tools": args.include_tools,
        "dry_run": args.dry_run,
        "progress": str(PROGRESS_PATH),
        "log": str(LOG_PATH),
    }, indent=2))

    if args.dry_run:
        return 0

    ok = 0
    for i, row in enumerate(remaining, 1):
        sid = row["id"]
        try:
            msgs = fetch_messages(conn, sid, args.include_tools)
            msgs = [m for m in msgs if clean_content(m, args.include_tools)]
            if not msgs:
                uploaded[sid] = {"status": "skipped_empty", "at": now_iso(), "source": row["source"]}
                save_progress(progress)
                continue
            transcript = format_transcript(row, msgs, args.include_tools)
            upload_session(client, cfg, row, transcript, len(msgs))
            uploaded[sid] = {
                "status": "uploaded",
                "at": now_iso(),
                "source": row["source"],
                "message_count": len(msgs),
                "bytes": len(transcript),
            }
            failed.pop(sid, None)
            ok += 1
            if ok % 10 == 0:
                print(f"uploaded={ok} last={sid} total_done={len(uploaded)} remaining_est={len(sessions)-len(uploaded)}", flush=True)
            log(f"uploaded {sid} source={row['source']} messages={len(msgs)} bytes={len(transcript)}")
        except Exception as e:
            failed[sid] = {"at": now_iso(), "source": row["source"], "error": repr(e)}
            log(f"FAILED {sid} source={row['source']} error={e!r}")
        finally:
            progress["updated_at"] = now_iso()
            save_progress(progress)
            if args.sleep:
                time.sleep(args.sleep)

    print(json.dumps({
        "uploaded_this_run": ok,
        "uploaded_total": len(uploaded),
        "failed_total": len(failed),
        "remaining_total": max(0, len(sessions) - len(uploaded)),
        "progress": str(PROGRESS_PATH),
        "log": str(LOG_PATH),
    }, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
