"""Durable post-response state lives atomically in SessionDB."""

import hashlib
import logging
import sqlite3
import time

import pytest

from hermes_state import SessionDB
from gateway import delivery_ledger as dl


@pytest.fixture
def db(tmp_path):
    store = SessionDB(db_path=tmp_path / "state.db")
    try:
        yield store
    finally:
        store.close()


def _delivery_kwargs(**overrides):
    values = {
        "obligation_id": "obligation-1",
        "session_key": "private-session-key",
        "session_lineage": "private-lineage-key",
        "platform": "slack",
        "chat_id": "C1",
        "thread_id": "T1",
        "content": "final response",
        "actions": [
            {"action_kind": "after_response", "payload": {"step": 1}},
            {"action_kind": "after_response", "payload": {"step": 2}},
        ],
    }
    values.update(overrides)
    return values


def _counts(db):
    with db._read_ctx() as conn:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("delivery_obligations", "gateway_post_response_actions")
        )


def test_creates_delivery_and_ordered_barrier_actions_atomically(db):
    created = db.create_final_delivery_and_barrier(**_delivery_kwargs())

    assert created.obligation_id == "obligation-1"
    assert _counts(db) == (1, 2)
    assert db.has_post_response_barrier("private-session-key", "private-lineage-key")
    with db._read_ctx() as conn:
        rows = conn.execute(
            "SELECT ordinal, action_kind, state, session_key_hash, session_lineage_hash, "
            "session_identifier_payload FROM gateway_post_response_actions "
            "ORDER BY ordinal"
        ).fetchall()
    assert [(row["ordinal"], row["action_kind"], row["state"]) for row in rows] == [
        (0, "after_response", "pending"),
        (1, "after_response", "pending"),
    ]
    assert rows[0]["session_key_hash"] != "private-session-key"
    assert rows[0]["session_lineage_hash"] != "private-lineage-key"
    assert rows[0]["session_identifier_payload"] == "private-session-key"


def test_exception_after_obligation_insert_rolls_back_everything(db):
    with pytest.raises(Exception):
        db.create_final_delivery_and_barrier(
            **_delivery_kwargs(actions=[{"action_kind": ""}])
        )
    assert _counts(db) == (0, 0)


def test_exception_after_first_action_rolls_back_everything(db):
    with pytest.raises(Exception):
        db.create_final_delivery_and_barrier(
            **_delivery_kwargs(
                actions=[
                    {"action_kind": "after_response", "payload": {"step": 1}},
                    {"action_kind": "", "payload": {"step": 2}},
                ]
            )
        )
    assert _counts(db) == (0, 0)


@pytest.mark.parametrize(
    ("target", "when"),
    [
        ("delivery_obligations", "1"),
        ("gateway_post_response_actions", "NEW.ordinal = 0"),
    ],
)
def test_sqlite_trigger_after_parent_or_first_action_rolls_back_everything(
    tmp_path, target, when
):
    """Faults after a real INSERT leave no partial receipt or barrier."""
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db._execute_write(
            lambda conn: conn.execute(
                f"""CREATE TRIGGER abort_after_real_insert
                    AFTER INSERT ON {target}
                    WHEN {when}
                    BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"""
            )
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
            db.create_final_delivery_and_barrier(**_delivery_kwargs())
    finally:
        db.close()

    reopened = SessionDB(db_path=path)
    try:
        assert _counts(reopened) == (0, 0)
    finally:
        reopened.close()


def test_sqlite_trigger_after_ordinal_zero_rolls_back_everything(tmp_path):
    """A later action fault also rolls back the parent and ordinal zero."""
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db._execute_write(
            lambda conn: conn.execute(
                """CREATE TRIGGER abort_after_second_action
                    AFTER INSERT ON gateway_post_response_actions
                    WHEN NEW.ordinal = 1
                    BEGIN SELECT RAISE(ABORT, 'injected ordinal one failure'); END"""
            )
        )
        with pytest.raises(sqlite3.IntegrityError, match="injected ordinal one failure"):
            db.create_final_delivery_and_barrier(**_delivery_kwargs())
    finally:
        db.close()

    reopened = SessionDB(db_path=path)
    try:
        assert _counts(reopened) == (0, 0)
    finally:
        reopened.close()


def test_deterministic_retry_is_idempotent_without_resetting_state_or_attempts(db):
    db.create_final_delivery_and_barrier(**_delivery_kwargs())
    first = db.claim_ready_post_response_actions(limit=1)[0]
    assert db.fail_post_response_action(
        first.action_key,
        owner_pid=first.owner_pid,
        owner_started_at=first.owner_started_at,
        claimed_attempt=first.attempts,
        error="transient",
        now=time.time(),
    )

    db.create_final_delivery_and_barrier(**_delivery_kwargs())
    with db._read_ctx() as conn:
        row = conn.execute(
            "SELECT state, attempts FROM gateway_post_response_actions "
            "WHERE action_key=?", (first.action_key,)
        ).fetchone()
    assert tuple(row) == ("failed", 1)


@pytest.mark.parametrize(
    "overrides",
    [
        {"platform": "discord"},
        {"chat_id": "different-chat"},
        {"thread_id": "different-thread"},
        {"content": "different final response"},
        {"session_key": "different-private-session-key"},
        {"session_lineage": "different-private-lineage-key"},
        {"actions": [{"action_kind": "after_response", "payload": {"step": 99}}]},
        {
            "actions": [
                {"action_kind": "after_response", "payload": {"step": 1}},
                {"action_kind": "changed", "payload": {"step": 2}},
            ]
        },
    ],
)
def test_changed_retry_is_rejected_atomically_without_mutating_existing_barrier(db, overrides):
    db.create_final_delivery_and_barrier(**_delivery_kwargs())
    claimed = db.claim_ready_post_response_actions(limit=1)[0]
    before = _counts(db)

    with pytest.raises(ValueError, match="does not match"):
        db.create_final_delivery_and_barrier(**_delivery_kwargs(**overrides))

    assert _counts(db) == before
    with db._read_ctx() as conn:
        row = conn.execute(
            "SELECT state, attempts, owner_pid, owner_started_at "
            "FROM gateway_post_response_actions WHERE action_key=?",
            (claimed.action_key,),
        ).fetchone()
    assert tuple(row) == ("running", 1, claimed.owner_pid, claimed.owner_started_at)


def test_action_key_collision_is_rejected_atomically(db):
    db.create_final_delivery_and_barrier(**_delivery_kwargs())
    with db._read_ctx() as conn:
        action_key = conn.execute(
            "SELECT action_key FROM gateway_post_response_actions WHERE ordinal=0"
        ).fetchone()[0]
    db._post_response_action_key = lambda *_args: action_key

    with pytest.raises(ValueError, match="action identity"):
        db.create_final_delivery_and_barrier(
            **_delivery_kwargs(obligation_id="different-obligation")
        )
    assert _counts(db) == (1, 2)


def test_foreign_key_prevents_orphan_post_response_action(db):
    assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(Exception):
        db._execute_write(
            lambda conn: conn.execute(
                "INSERT INTO gateway_post_response_actions "
                "(obligation_id, ordinal, action_key, action_kind, session_key_hash, session_lineage_hash, "
                "session_identifier_payload, payload_json, state, attempts, created_at, updated_at) "
                "VALUES ('missing', 0, 'orphan', 'after_response', 'key-hash', 'hash', 'payload', '{}', "
                "'pending', 0, 1, 1)"
            )
        )


def test_actions_claim_strictly_in_ordinal_order(db):
    db.create_final_delivery_and_barrier(**_delivery_kwargs())
    assert [action.ordinal for action in db.list_ready_post_response_actions(limit=10)] == [0]
    first = db.claim_ready_post_response_actions(limit=10)
    assert [action.ordinal for action in first] == [0]
    assert db.complete_post_response_action(
        first[0].action_key,
        owner_pid=first[0].owner_pid,
        owner_started_at=first[0].owner_started_at,
        claimed_attempt=first[0].attempts,
    )
    second = db.claim_ready_post_response_actions(limit=10)
    assert [action.ordinal for action in second] == [1]


@pytest.mark.parametrize(
    ("delivered", "expected_ready_ordinals"),
    [
        (False, []),
        (True, [1]),
    ],
)
def test_finalize_turn_requires_delivered_parent_receipt(
    db, delivered, expected_ready_ordinals,
):
    """A failed final receipt remains an admission barrier until recovery."""
    db.create_final_delivery_and_barrier(
        **_delivery_kwargs(
            actions=[
                {"action_kind": "after_response", "payload": {"step": 1}},
                {"action_kind": "finalize_turn", "payload": {"step": 2}},
            ],
        )
    )
    first = db.claim_ready_post_response_actions(limit=1, now=100.0)[0]
    assert first.action_kind == "after_response"
    assert db.complete_post_response_action(
        first.action_key,
        owner_pid=first.owner_pid,
        owner_started_at=first.owner_started_at,
        claimed_attempt=first.attempts,
    )
    assert db.mark_final_delivery_attempting("obligation-1")
    assert db.settle_final_delivery(
        "obligation-1", delivered=delivered, error="injected delivery failure",
    )

    assert [
        action.ordinal
        for action in db.list_ready_post_response_actions(limit=10, now=1000.0)
    ] == expected_ready_ordinals


def test_abandoned_parent_receipt_remains_an_inspectable_fail_closed_barrier(db):
    """Poison receipts retain their parent row and cannot clear finalization."""
    db.create_final_delivery_and_barrier(
        **_delivery_kwargs(actions=[{"action_kind": "finalize_turn"}])
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE delivery_obligations SET state='abandoned' WHERE obligation_id=?",
            ("obligation-1",),
        )
    )

    assert db.claim_ready_post_response_actions(limit=1) == []
    assert db.has_post_response_barrier("private-session-key", "private-lineage-key")
    with db._read_ctx() as conn:
        receipt = conn.execute(
            "SELECT state FROM delivery_obligations WHERE obligation_id=?",
            ("obligation-1",),
        ).fetchone()
    assert receipt["state"] == "abandoned"


def test_two_claimers_cannot_own_one_action_and_stale_owner_cannot_complete(db, monkeypatch):
    db.create_final_delivery_and_barrier(**_delivery_kwargs(actions=[{"action_kind": "after_response"}]))
    first = db.claim_ready_post_response_actions(limit=1)[0]
    assert db.claim_ready_post_response_actions(limit=1) == []

    monkeypatch.setattr(db, "_post_response_owner_alive", lambda *_args: False)
    monkeypatch.setattr(db, "_post_response_owner_stamp", lambda: (222, 2))
    reclaimed = db.claim_ready_post_response_actions(limit=1)[0]
    assert reclaimed.action_key == first.action_key
    assert (reclaimed.owner_pid, reclaimed.owner_started_at) == (222, 2)
    assert not db.complete_post_response_action(
        first.action_key,
        owner_pid=first.owner_pid,
        owner_started_at=first.owner_started_at,
        claimed_attempt=first.attempts,
    )


def test_same_owner_stale_attempt_cannot_complete_or_fail_reclaimed_action(db, monkeypatch):
    """PID/start identity is insufficient when one worker retries an action."""
    db.create_final_delivery_and_barrier(
        **_delivery_kwargs(actions=[{"action_kind": "after_response"}])
    )
    monkeypatch.setattr(db, "_post_response_owner_stamp", lambda: (123, 456))
    first = db.claim_ready_post_response_actions(limit=1)[0]
    monkeypatch.setattr(db, "_post_response_owner_alive", lambda *_args: False)
    second = db.claim_ready_post_response_actions(limit=1)[0]

    assert (first.owner_pid, first.owner_started_at) == (
        second.owner_pid,
        second.owner_started_at,
    )
    assert first.claim_attempt == first.attempts
    assert second.claim_attempt == second.attempts
    assert second.attempts == first.attempts + 1
    assert not db.complete_post_response_action(
        first.action_key,
        owner_pid=first.owner_pid,
        owner_started_at=first.owner_started_at,
        claimed_attempt=first.attempts,
    )
    assert not db.fail_post_response_action(
        first.action_key,
        owner_pid=first.owner_pid,
        owner_started_at=first.owner_started_at,
        claimed_attempt=first.attempts,
        error="delayed first attempt",
    )
    with db._read_ctx() as conn:
        row = conn.execute(
            "SELECT state, attempts FROM gateway_post_response_actions WHERE action_key=?",
            (first.action_key,),
        ).fetchone()
    assert tuple(row) == ("running", second.attempts)


def test_owner_liveness_unknown_fails_closed_and_pid_reuse_is_dead(monkeypatch):
    import gateway.status as status

    monkeypatch.setattr(status, "get_process_start_time", lambda _pid: None)
    monkeypatch.setattr(status, "_pid_exists", lambda _pid: (_ for _ in ()).throw(OSError()))
    assert SessionDB._post_response_owner_alive(123, 100)

    monkeypatch.setattr(status, "_pid_exists", lambda _pid: False)
    assert not SessionDB._post_response_owner_alive(123, 100)

    monkeypatch.setattr(status, "get_process_start_time", lambda _pid: 100)
    assert SessionDB._post_response_owner_alive(123, 100)

    monkeypatch.setattr(status, "get_process_start_time", lambda _pid: 101)
    assert not SessionDB._post_response_owner_alive(123, 100)


def test_fail_uses_bounded_retry_and_retains_barrier_until_completion(db):
    db.create_final_delivery_and_barrier(**_delivery_kwargs(actions=[{"action_kind": "after_response"}]))
    claimed = db.claim_ready_post_response_actions(limit=1, now=100.0)[0]
    assert db.fail_post_response_action(
        claimed.action_key,
        owner_pid=claimed.owner_pid,
        owner_started_at=claimed.owner_started_at,
        claimed_attempt=claimed.attempts,
        error="temporary",
        now=100.0,
    )
    assert db.has_post_response_barrier("private-session-key", "private-lineage-key")
    assert db.claim_ready_post_response_actions(limit=1, now=100.1) == []
    retry = db.claim_ready_post_response_actions(limit=1, now=1000.0)[0]
    assert retry.attempts == 2
    assert db.complete_post_response_action(
        retry.action_key,
        owner_pid=retry.owner_pid,
        owner_started_at=retry.owner_started_at,
        claimed_attempt=retry.attempts,
    )
    assert not db.has_post_response_barrier("private-session-key", "private-lineage-key")


def test_terminal_failure_blocks_later_ordinals_without_rescheduling(db):
    db.create_final_delivery_and_barrier(**_delivery_kwargs())
    claimed = db.claim_ready_post_response_actions(limit=1, now=100.0)[0]
    assert db.fail_post_response_action(
        claimed.action_key,
        owner_pid=claimed.owner_pid,
        owner_started_at=claimed.owner_started_at,
        claimed_attempt=claimed.attempts,
        error="private-session-key terminal failure",
        now=100.0,
        max_attempts=1,
    )

    assert db.list_ready_post_response_actions(limit=10, now=10_000.0) == []
    assert db.claim_ready_post_response_actions(limit=10, now=10_000.0) == []
    assert db.has_post_response_barrier("private-session-key", "private-lineage-key")
    diagnostics = db.get_post_response_barrier_diagnostics("obligation-1")
    assert diagnostics == [
        {
            "obligation_id": "obligation-1",
            "ordinal": 0,
            "action_kind": "after_response",
            "state": "blocked",
            "attempts": 1,
                "last_error": "sha256:" + hashlib.sha256(
                    b"private-session-key terminal failure"
                ).hexdigest(),
        },
        {
            "obligation_id": "obligation-1",
            "ordinal": 1,
            "action_kind": "after_response",
            "state": "pending",
            "attempts": 0,
            "last_error": None,
        },
    ]
    assert "private-session-key" not in repr(diagnostics)


def test_huge_legacy_attempt_count_fails_closed_without_overflow(db):
    """A corrupted count must block the owned action rather than strand it running."""
    db.create_final_delivery_and_barrier(
        **_delivery_kwargs(actions=[{"action_kind": "after_response"}])
    )
    claimed = db.claim_ready_post_response_actions(limit=1, now=100.0)[0]
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions SET attempts=? WHERE action_key=?",
            (1_000_000, claimed.action_key),
        )
    )

    assert db.fail_post_response_action(
        claimed.action_key,
        owner_pid=claimed.owner_pid,
        owner_started_at=claimed.owner_started_at,
        claimed_attempt=1_000_000,
        error="legacy counter",
        now=100.0,
    )
    with db._read_ctx() as conn:
        row = conn.execute(
            "SELECT state, attempts, next_retry_at FROM gateway_post_response_actions "
            "WHERE action_key=?",
            (claimed.action_key,),
        ).fetchone()
    assert tuple(row) == ("blocked", 1_000_000, None)


def test_ready_scan_does_not_starve_pending_work_behind_live_running_rows(db, monkeypatch):
    """Live reclaim candidates cannot consume the bounded scan before ready work."""
    for obligation_id in ("a", "b", "c", "d", "e"):
        db.create_final_delivery_and_barrier(
            **_delivery_kwargs(
                obligation_id=obligation_id,
                actions=[{"action_kind": "after_response"}],
            )
        )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions "
            "SET state='running', owner_pid=999, owner_started_at=1 "
            "WHERE obligation_id IN ('a', 'b', 'c', 'd')"
        )
    )
    monkeypatch.setattr(db, "_post_response_owner_alive", lambda *_args: True)

    assert [action.obligation_id for action in db.list_ready_post_response_actions(
        limit=1, now=100.0
    )] == ["e"]
    assert [action.obligation_id for action in db.claim_ready_post_response_actions(
        limit=1, now=100.0
    )] == ["e"]


@pytest.mark.parametrize("operation", ["list", "claim"])
def test_ready_scan_reaches_stale_work_after_more_than_eighty_live_running_rows(
    db, monkeypatch, operation
):
    """A bounded scan must paginate past a live-running prefix."""
    for number in range(82):
        db.create_final_delivery_and_barrier(
            **_delivery_kwargs(
                obligation_id=f"scan-{number:03d}",
                actions=[{"action_kind": "after_response"}],
            )
        )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions "
            "SET state='running', owner_pid=999, owner_started_at=1"
        )
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions "
            "SET owner_pid=998 WHERE obligation_id='scan-081'"
        )
    )
    monkeypatch.setattr(db, "_post_response_owner_alive", lambda pid, *_args: pid == 999)

    method = (
        db.list_ready_post_response_actions
        if operation == "list"
        else db.claim_ready_post_response_actions
    )
    assert [action.obligation_id for action in method(limit=1, now=100.0)] == ["scan-081"]


def test_warning_logs_do_not_disclose_raw_session_identifier(db, caplog):
    db.create_final_delivery_and_barrier(**_delivery_kwargs(actions=[{"action_kind": "after_response"}]))
    claimed = db.claim_ready_post_response_actions(limit=1)[0]
    with caplog.at_level(logging.WARNING):
        assert db.fail_post_response_action(
            claimed.action_key,
            owner_pid=claimed.owner_pid,
            owner_started_at=claimed.owner_started_at,
            claimed_attempt=claimed.attempts,
            error="private-session-key " + ("x" * 1000),
        )
    assert "private-session-key" not in caplog.text
    with db._read_ctx() as conn:
        last_error = conn.execute(
            "SELECT last_error FROM gateway_post_response_actions WHERE action_key=?",
            (claimed.action_key,),
        ).fetchone()[0]
    assert "private-session-key" not in last_error
    assert last_error.startswith("sha256:")
    assert len(last_error) == len("sha256:") + 64


def test_failure_and_diagnostics_redact_session_key_and_lineage(db):
    db.create_final_delivery_and_barrier(**_delivery_kwargs(actions=[{"action_kind": "after_response"}]))
    claimed = db.claim_ready_post_response_actions(limit=1)[0]
    raw_key = "private-session-key"
    raw_lineage = "private-lineage-key"

    assert db.fail_post_response_action(
        claimed.action_key,
        owner_pid=claimed.owner_pid,
        owner_started_at=claimed.owner_started_at,
        claimed_attempt=claimed.attempts,
        error=f"failed for {raw_key} through {raw_lineage}",
    )
    with db._read_ctx() as conn:
        row = conn.execute(
            "SELECT session_lineage_payload, last_error "
            "FROM gateway_post_response_actions WHERE action_key=?",
            (claimed.action_key,),
        ).fetchone()
    assert row["session_lineage_payload"] == raw_lineage
    assert raw_key not in row["last_error"]
    assert raw_lineage not in row["last_error"]
    diagnostics = db.get_post_response_barrier_diagnostics("obligation-1")
    assert raw_key not in repr(diagnostics)
    assert raw_lineage not in repr(diagnostics)
    assert raw_key not in repr(claimed)
    assert raw_lineage not in repr(claimed)


def test_action_failure_stores_only_stable_error_token_for_unknown_legacy_lineage(db):
    db.create_final_delivery_and_barrier(
        **_delivery_kwargs(actions=[{"action_kind": "after_response"}])
    )
    claimed = db.claim_ready_post_response_actions(limit=1)[0]
    unknown_legacy_lineage = "legacy-lineage-that-must-never-leak"
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions SET session_lineage_payload=? "
            "WHERE action_key=?",
            (unknown_legacy_lineage, claimed.action_key),
        )
    )
    error = f"dispatch failed for {unknown_legacy_lineage} and another-secret"
    assert db.fail_post_response_action(
        claimed.action_key,
        owner_pid=claimed.owner_pid,
        owner_started_at=claimed.owner_started_at,
        claimed_attempt=claimed.attempts,
        error=error,
    )
    with db._read_ctx() as conn:
        stored = conn.execute(
            "SELECT last_error FROM gateway_post_response_actions WHERE action_key=?",
            (claimed.action_key,),
        ).fetchone()[0]
    assert stored == "sha256:" + hashlib.sha256(
        error.encode("utf-8", "replace")
    ).hexdigest()
    assert unknown_legacy_lineage not in stored
    assert "another-secret" not in stored


@pytest.mark.parametrize("action_state", ["pending", "failed", "blocked", "running"])
def test_retention_pruning_keeps_parent_with_any_non_done_action(
    db, monkeypatch, action_state
):
    monkeypatch.setattr(dl, "_db_path", lambda: db.db_path)
    db.create_final_delivery_and_barrier(**_delivery_kwargs())
    db.create_final_delivery_and_barrier(
        **_delivery_kwargs(obligation_id="all-done", actions=[{"action_kind": "after_response"}])
    )
    old = time.time() - dl._RETENTION_SECONDS - 1
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE delivery_obligations SET state='delivered', updated_at=?",
            (old,),
        )
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions SET state='done' "
            "WHERE obligation_id='obligation-1' AND ordinal=0"
        )
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions SET state=? "
            "WHERE obligation_id='obligation-1' AND ordinal=1",
            (action_state,),
        )
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions SET state='done' "
            "WHERE obligation_id='all-done'"
        )
    )

    dl._prune(now=time.time())

    with db._read_ctx() as conn:
        assert conn.execute(
            "SELECT 1 FROM delivery_obligations WHERE obligation_id='obligation-1'"
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM delivery_obligations WHERE obligation_id='all-done'"
        ).fetchone() is None


@pytest.mark.parametrize("action_state", ["pending", "failed", "blocked", "running"])
def test_row_cap_pruning_keeps_parent_with_any_non_done_action(
    db, monkeypatch, action_state
):
    monkeypatch.setattr(dl, "_db_path", lambda: db.db_path)
    monkeypatch.setattr(dl, "_MAX_ROWS", 1)
    db.create_final_delivery_and_barrier(**_delivery_kwargs())
    db.create_final_delivery_and_barrier(
        **_delivery_kwargs(obligation_id="all-done", actions=[{"action_kind": "after_response"}])
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE delivery_obligations SET state='delivered', updated_at=0"
        )
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions SET state=? "
            "WHERE obligation_id='obligation-1' AND ordinal=1",
            (action_state,),
        )
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions SET state='done' "
            "WHERE obligation_id='obligation-1' AND ordinal=0"
        )
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE gateway_post_response_actions SET state='done' "
            "WHERE obligation_id='all-done'"
        )
    )

    dl._prune(now=0)

    with db._read_ctx() as conn:
        assert conn.execute(
            "SELECT 1 FROM delivery_obligations WHERE obligation_id='obligation-1'"
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM delivery_obligations WHERE obligation_id='all-done'"
        ).fetchone() is None


def test_migrates_prechange_delivery_ledger_database_and_reopens_without_orphans(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (26)")
        conn.execute(
            """CREATE TABLE delivery_obligations (
                obligation_id TEXT PRIMARY KEY, session_key TEXT NOT NULL,
                platform TEXT NOT NULL, chat_id TEXT NOT NULL, thread_id TEXT,
                content TEXT NOT NULL, state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, owner_pid INTEGER,
                owner_started_at INTEGER, last_error TEXT
            )"""
        )
        conn.execute(
            """INSERT INTO delivery_obligations
               VALUES ('ledger-obligation', 'ledger-session', 'slack', 'C1', NULL,
                       'content', 'pending', 0, 1, 1, NULL, NULL, NULL)"""
        )
        conn.commit()
    finally:
        conn.close()

    db = SessionDB(db_path=path)
    try:
        assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        db.create_final_delivery_and_barrier(**_delivery_kwargs())
    finally:
        db.close()

    reopened = SessionDB(db_path=path)
    try:
        assert reopened._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert reopened._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        reopened.close()


def test_reopens_early_action_table_with_terminal_blocked_state(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db.create_final_delivery_and_barrier(**_delivery_kwargs())
    finally:
        db.close()

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "ALTER TABLE gateway_post_response_actions RENAME TO actions_current"
        )
        conn.execute(
            """CREATE TABLE gateway_post_response_actions (
                obligation_id TEXT NOT NULL REFERENCES delivery_obligations(obligation_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0), action_key TEXT NOT NULL UNIQUE,
                action_kind TEXT NOT NULL CHECK (length(action_kind) > 0),
                session_key_hash TEXT NOT NULL, session_lineage_hash TEXT NOT NULL,
                session_identifier_payload TEXT NOT NULL, payload_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done', 'failed')),
                owner_pid INTEGER, owner_started_at INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                next_retry_at REAL, last_error TEXT, PRIMARY KEY (obligation_id, ordinal)
            )"""
        )
        conn.execute(
            """INSERT INTO gateway_post_response_actions (
                   obligation_id, ordinal, action_key, action_kind,
                   session_key_hash, session_lineage_hash,
                   session_identifier_payload, payload_json, state, owner_pid,
                   owner_started_at, attempts, created_at, updated_at,
                   next_retry_at, last_error
               )
               SELECT obligation_id, ordinal, action_key, action_kind,
                      session_key_hash, session_lineage_hash,
                      session_identifier_payload, payload_json, state, owner_pid,
                      owner_started_at, attempts, created_at, updated_at,
                      next_retry_at, last_error
               FROM actions_current"""
        )
        conn.execute("DROP TABLE actions_current")
        conn.execute("UPDATE schema_version SET version=26")
        conn.commit()
    finally:
        conn.close()

    reopened = SessionDB(db_path=path)
    try:
        columns = {
            row[1]
            for row in reopened._conn.execute(
                "PRAGMA table_info(gateway_post_response_actions)"
            )
        }
        assert "session_lineage_payload" in columns
        table_sql = reopened._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='gateway_post_response_actions'"
        ).fetchone()[0]
        assert "'blocked'" in table_sql
        assert reopened._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert reopened._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        claimed = reopened.claim_ready_post_response_actions(limit=1, now=100.0)[0]
        assert reopened.fail_post_response_action(
            claimed.action_key,
            owner_pid=claimed.owner_pid,
            owner_started_at=claimed.owner_started_at,
            claimed_attempt=claimed.attempts,
            error="terminal",
            now=100.0,
            max_attempts=1,
        )
        assert reopened.get_post_response_barrier_diagnostics("obligation-1")[0]["state"] == "blocked"
    finally:
        reopened.close()


def test_action_state_rebuild_ignores_unrelated_historical_fk_violation(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db.create_final_delivery_and_barrier(
            **_delivery_kwargs(actions=[{"action_kind": "after_response"}])
        )
    finally:
        db.close()

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ALTER TABLE gateway_post_response_actions RENAME TO actions_current")
        conn.execute(
            """CREATE TABLE gateway_post_response_actions (
                obligation_id TEXT NOT NULL REFERENCES delivery_obligations(obligation_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0), action_key TEXT NOT NULL UNIQUE,
                action_kind TEXT NOT NULL CHECK (length(action_kind) > 0),
                session_key_hash TEXT NOT NULL, session_lineage_hash TEXT NOT NULL,
                session_identifier_payload TEXT NOT NULL, session_lineage_payload TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done', 'failed')),
                owner_pid INTEGER, owner_started_at INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                next_retry_at REAL, last_error TEXT, PRIMARY KEY (obligation_id, ordinal)
            )"""
        )
        conn.execute("INSERT INTO gateway_post_response_actions SELECT * FROM actions_current")
        conn.execute("DROP TABLE actions_current")
        conn.execute("CREATE TABLE unrelated_parent (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE unrelated_child (parent_id TEXT REFERENCES unrelated_parent(id))")
        conn.execute("INSERT INTO unrelated_child VALUES ('historical-orphan')")
        conn.commit()
    finally:
        conn.close()

    reopened = SessionDB(db_path=path)
    try:
        assert "'blocked'" in reopened._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='gateway_post_response_actions'"
        ).fetchone()[0]
        assert reopened._conn.execute("PRAGMA foreign_key_check(unrelated_child)").fetchall()
        assert reopened._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='gateway_post_response_actions_legacy'"
        ).fetchone() is None
    finally:
        reopened.close()


def test_orphan_legacy_actions_fail_closed_preserve_evidence_then_migrate_after_repair(
    tmp_path,
):
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db.create_final_delivery_and_barrier(
            **_delivery_kwargs(actions=[{"action_kind": "after_response"}])
        )
    finally:
        db.close()

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "ALTER TABLE gateway_post_response_actions RENAME TO actions_current"
        )
        conn.execute(
            """CREATE TABLE gateway_post_response_actions (
                obligation_id TEXT NOT NULL REFERENCES delivery_obligations(obligation_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0), action_key TEXT NOT NULL UNIQUE,
                action_kind TEXT NOT NULL CHECK (length(action_kind) > 0),
                session_key_hash TEXT NOT NULL, session_lineage_hash TEXT NOT NULL,
                session_identifier_payload TEXT NOT NULL, session_lineage_payload TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done', 'failed')),
                owner_pid INTEGER, owner_started_at INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                next_retry_at REAL, last_error TEXT, PRIMARY KEY (obligation_id, ordinal)
            )"""
        )
        conn.execute(
            """INSERT INTO gateway_post_response_actions
               SELECT * FROM actions_current"""
        )
        conn.execute(
            """INSERT INTO gateway_post_response_actions (
                   obligation_id, ordinal, action_key, action_kind,
                   session_key_hash, session_lineage_hash,
                   session_identifier_payload, session_lineage_payload,
                   payload_json, state, owner_pid, owner_started_at,
                   attempts, created_at, updated_at, next_retry_at, last_error
               )
               SELECT 'orphan-obligation', 0, 'orphan-action', action_kind,
                      session_key_hash, session_lineage_hash,
                      session_identifier_payload, session_lineage_payload,
                      payload_json, state, owner_pid, owner_started_at,
                      attempts, created_at, updated_at, next_retry_at, last_error
               FROM actions_current LIMIT 1"""
        )
        conn.execute("DROP TABLE actions_current")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(sqlite3.IntegrityError, match="orphan obligations"):
        SessionDB(db_path=path)

    conn = sqlite3.connect(path)
    try:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='gateway_post_response_actions'"
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='gateway_post_response_actions_legacy'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT obligation_id FROM gateway_post_response_actions "
            "WHERE action_key='orphan-action'"
        ).fetchone()[0] == "orphan-obligation"
        conn.execute(
            """INSERT INTO delivery_obligations (
                   obligation_id, session_key, platform, chat_id, thread_id,
                   content, state, attempts, created_at, updated_at
               ) VALUES ('orphan-obligation', 'repair-session', 'slack', 'C2', NULL,
                         'operator repair', 'pending', 0, 1, 1)"""
        )
        conn.commit()
    finally:
        conn.close()

    repaired = SessionDB(db_path=path)
    try:
        table_sql = repaired._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='gateway_post_response_actions'"
        ).fetchone()[0]
        assert "'blocked'" in table_sql
        assert repaired._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        repaired.close()


def test_failed_action_table_rebuild_rolls_back_without_legacy_table_artifact(tmp_path):
    path = tmp_path / "state.db"
    db = SessionDB(db_path=path)
    try:
        db.create_final_delivery_and_barrier(
            **_delivery_kwargs(actions=[{"action_kind": "after_response"}])
        )
    finally:
        db.close()

    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute(
            "ALTER TABLE gateway_post_response_actions RENAME TO actions_current"
        )
        conn.execute(
            """CREATE TABLE gateway_post_response_actions (
                obligation_id TEXT NOT NULL REFERENCES delivery_obligations(obligation_id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL CHECK (ordinal >= 0), action_key TEXT NOT NULL UNIQUE,
                action_kind TEXT NOT NULL CHECK (length(action_kind) > 0),
                session_key_hash TEXT NOT NULL, session_lineage_hash TEXT NOT NULL,
                session_identifier_payload TEXT NOT NULL, session_lineage_payload TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done', 'failed')),
                owner_pid INTEGER, owner_started_at INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                next_retry_at REAL, last_error TEXT, PRIMARY KEY (obligation_id, ordinal)
            )"""
        )
        conn.execute("INSERT INTO gateway_post_response_actions SELECT * FROM actions_current")
        conn.execute(
            "UPDATE gateway_post_response_actions SET attempts=-1 WHERE ordinal=0"
        )
        conn.execute("DROP TABLE actions_current")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(sqlite3.IntegrityError):
        SessionDB(db_path=path)

    conn = sqlite3.connect(path)
    try:
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='gateway_post_response_actions'"
        ).fetchone() is not None
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='gateway_post_response_actions_legacy'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT attempts FROM gateway_post_response_actions"
        ).fetchone()[0] == -1
        conn.execute("UPDATE gateway_post_response_actions SET attempts=0")
        conn.commit()
    finally:
        conn.close()

    repaired = SessionDB(db_path=path)
    try:
        assert repaired._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert repaired._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        repaired.close()


def test_delivery_ledger_can_write_sessiondb_initialized_delivery_table(db, monkeypatch):
    from gateway import delivery_ledger

    monkeypatch.setattr(delivery_ledger, "_db_path", lambda: db.db_path)
    delivery_ledger.record_obligation(
        obligation_id="ledger-write", session_key="ledger-session", platform="slack",
        chat_id="C2", thread_id=None, content="ledger content",
    )
    assert delivery_ledger.mark_attempting("ledger-write")
    delivery_ledger.mark_delivered("ledger-write")
    with db._read_ctx() as conn:
        assert conn.execute(
            "SELECT state FROM delivery_obligations WHERE obligation_id='ledger-write'"
        ).fetchone()[0] == "delivered"
