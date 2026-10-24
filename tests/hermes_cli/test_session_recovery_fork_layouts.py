"""Recover the retained fork's actual SQLite record order, before and after upgrade."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from hermes_cli.session_lost_and_found import map_lost_and_found_rows
from hermes_cli.session_recovery import recover_session_database
from hermes_state import SessionDB

# Verbatim messages DDL from fork 2c31abf2bcb4a6fa0abc868f707ec09a053cb1f0,
# hermes_state_common.py. Keep independent of the recovery history under test.
_FORK_MESSAGES_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    effect_disposition TEXT,
    _side_effect_evidence_succeeded INTEGER,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    reasoning_details TEXT,
    codex_reasoning_items TEXT,
    codex_message_items TEXT,
    platform_message_id TEXT,
    observed INTEGER DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0,
    api_content TEXT,
    display_kind TEXT,
    display_metadata TEXT
);
"""
# Verbatim messages DDL from c1cafdc7441290a55e7e507af6d591b09a08a556^.
# Existing profiles received the new field through ALTER ADD, not CREATE.
_PRE_FORK_MESSAGES_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    effect_disposition TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    reasoning_details TEXT,
    codex_reasoning_items TEXT,
    codex_message_items TEXT,
    platform_message_id TEXT,
    observed INTEGER DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0,
    api_content TEXT,
    display_kind TEXT,
    display_metadata TEXT
);
"""
_SESSION = "20260909_171449_abc001"
_VALUES = {
    "session_id": _SESSION, "role": "tool", "content": "successful terminal result",
    "tool_call_id": "call-1", "tool_name": "terminal", "effect_disposition": "completed",
    "_side_effect_evidence_succeeded": 1, "timestamp": 1792000000.0,
    "token_count": 4, "observed": 0, "active": 1, "compacted": 0,
    "api_content": '[{"type":"text","text":"API result"}]',
    "display_kind": "tool", "display_metadata": '{"label":"retained result"}',
}


def _insert_message(conn: sqlite3.Connection, timestamp: float, *, before_fork: bool = False) -> None:
    values = {**_VALUES, "timestamp": timestamp}
    if before_fork:
        del values["_side_effect_evidence_succeeded"]
    names = ','.join(values)
    placeholders = ','.join('?' for _ in values)
    conn.execute(f'INSERT INTO messages ({names}) VALUES ({placeholders})', list(values.values()))
    conn.commit()


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for index in range(9):
        byte = data[offset]
        offset += 1
        value = (value << (8 if index == 8 else 7)) | (byte if index == 8 else byte & 127)
        if byte < 128 or index == 8:
            return value, offset
    raise AssertionError("unreachable SQLite varint")


def _decode_record(payload: bytes) -> tuple:
    header_size, offset = _varint(payload, 0)
    serials = []
    while offset < header_size:
        serial, offset = _varint(payload, offset)
        serials.append(serial)
    offset = header_size
    values = []
    for serial in serials:
        if serial in (0, 8, 9):
            values.append({0: None, 8: 0, 9: 1}[serial])
            continue
        size = (serial - 12) // 2 if serial >= 12 else {1: 1, 2: 2, 3: 3, 4: 4, 5: 6, 6: 8, 7: 8}[serial]
        cell = payload[offset:offset + size]
        offset += size
        if serial >= 12:
            value = cell.decode("utf-8") if serial % 2 else cell
        else:
            value = struct.unpack(">d", cell)[0] if serial == 7 else int.from_bytes(cell, "big", signed=True)
        values.append(value)
    assert offset == len(payload)
    return tuple(values)


def _page_records(path: Path, root: int) -> list[tuple[int, tuple]]:
    """Read actual leaf-cell payloads, including short records left by ALTER ADD.

    This tiny fixture stays on one page without overflow. Reading SQL rows instead
    would synthesize added defaults and hide the old 24-field physical records.
    """
    data = path.read_bytes()
    page_size = int.from_bytes(data[16:18], "big")
    if page_size == 1:
        page_size = 65536
    page = data[(root - 1) * page_size:root * page_size]
    assert page[0] == 13  # table b-tree leaf, not an interior or index page
    count = int.from_bytes(page[3:5], "big")
    records = []
    for index in range(count):
        start = int.from_bytes(page[8 + 2 * index:10 + 2 * index], "big")
        size, offset = _varint(page, start)
        rowid, offset = _varint(page, offset)
        assert offset + size <= page_size  # fixture has no overflow payload
        records.append((rowid, _decode_record(page[offset:offset + size])))
    return records


@pytest.mark.parametrize("upgrade", [False, True], ids=["original-fork", "current-migration"])
@pytest.mark.parametrize("timestamp", [1792000000.0, 1792000000.25], ids=["integer-record", "real-record"])
@pytest.mark.parametrize("legacy", [False, True], ids=["fresh-profile", "existing-profile"])
def test_fork_physical_records_recover_by_column_identity(
    tmp_path: Path, upgrade: bool, timestamp: float, legacy: bool,
) -> None:
    source = tmp_path / "fork.db"
    with sqlite3.connect(source) as conn:
        conn.execute(_PRE_FORK_MESSAGES_SQL if legacy else _FORK_MESSAGES_SQL)
        if legacy:
            _insert_message(conn, timestamp, before_fork=True)
            # Actual original _reconcile_columns migration in c1cafdc7441:
            # the missing INTEGER column is appended to the existing table.
            conn.execute("ALTER TABLE messages ADD COLUMN _side_effect_evidence_succeeded INTEGER")
        _insert_message(conn, timestamp)
    if upgrade:
        # Invoke the real current migration; do not manufacture the resulting order.
        with SessionDB(source) as db:
            db.create_session(_SESSION, source="cli")
        with sqlite3.connect(source) as conn:
            _insert_message(conn, timestamp)
    with sqlite3.connect(source) as conn:
        root = conn.execute("SELECT rootpage FROM sqlite_master WHERE name='messages'").fetchone()[0]
        names = [row[1] for row in conn.execute("PRAGMA table_info(messages)")]
        sql_timestamps = conn.execute("SELECT timestamp,typeof(timestamp) FROM messages ORDER BY id").fetchall()
        assert sql_timestamps == [(timestamp, "real")] * (1 + legacy + upgrade)
        assert all(type(value) is float for value, _ in sql_timestamps)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    original_bytes = source.read_bytes()
    records = _page_records(source, root)
    assert [len(cells) for _, cells in records] == ([23] if legacy else []) + [24] + ([31] if upgrade else [])
    assert names[23 if legacy else 8] == "_side_effect_evidence_succeeded"
    assert type(records[0][1][8 if legacy else 9]) is (int if timestamp.is_integer() else float)

    target = tmp_path / "recovered.db"
    with SessionDB(target) as db:
        db.create_session(_SESSION, source="cli")
    with sqlite3.connect(":memory:") as lf, sqlite3.connect(target, isolation_level=None) as dest:
        width = max(len(cells) for _, cells in records)
        lf.execute("CREATE TABLE lost_and_found (rootpgno, pgno, nfield, id, "
                   + ','.join(f'c{index}' for index in range(width)) + ')')
        for rowid, cells in records:
            lf.execute("INSERT INTO lost_and_found VALUES ("
                       + ','.join('?' for _ in range(width + 4)) + ')',
                       [root, root, len(cells), rowid, *cells, *([None] * (width - len(cells)))])
        report = map_lost_and_found_rows(lf, dest)
        assert report["mapped_by_layout"] == len(records)
        assert report["unrecognized_layout_rows"] == 0
        for _, cells in records:
            expected_layout = names[:len(cells)]
            if legacy and len(cells) == 24:
                # This INTEGER is physically indistinguishable from upstream's
                # appended summary flag. Later records may remain ambiguous too.
                expected_layout[23] = None
            elif legacy and len(cells) == 31:
                expected_layout[23:27] = [None] * 4
            assert report["inferred_layouts"]["messages"][str(len(cells))] == expected_layout
        recovered = dest.execute(
            "SELECT active,compacted,_compressed_summary,_side_effect_evidence_succeeded,timestamp,"
            "content,tool_call_id,tool_name,effect_disposition,api_content,display_kind,display_metadata "
            "FROM messages ORDER BY id"
        ).fetchall()
        expected = (1, 0, 0, 1, timestamp, *[_VALUES[name] for name in (
            "content", "tool_call_id", "tool_name", "effect_disposition", "api_content", "display_kind", "display_metadata"
        )])
        expected_rows = [expected] * len(records)
        if legacy:
            expected_rows[0] = (*expected[:3], None, *expected[4:])
            expected_rows = [expected_rows[0]] * len(records)
            assert report["ambiguous_layout_rows"] == (2 if upgrade else 1)
            assert report["ambiguous_layout_positions"] == {
                "messages": {"24": [23], **({"31": [23, 24, 26]} if upgrade else {})}
            }
        assert recovered == expected_rows
    if upgrade:
        # A readable schema supplies authentic names and preserves the same
        # evidence exactly; schema-less ambiguity must never fabricate proof.
        named_output = tmp_path / "named-recovery.db"
        report = recover_session_database(source, named_output, work_dir=tmp_path)
        assert report["verified"] is True
        with sqlite3.connect(named_output) as conn:
            evidence = conn.execute(
                "SELECT _side_effect_evidence_succeeded,_compressed_summary FROM messages ORDER BY id"
            ).fetchall()
        assert evidence == ([(None, 0)] if legacy else []) + [(1, 0)] * (1 + upgrade)
    assert source.read_bytes() == original_bytes
