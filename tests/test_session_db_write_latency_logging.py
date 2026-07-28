import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from hermes_state import SessionDB


def test_execute_write_warns_when_waiting_for_session_db_lock(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    def insert_session(conn):
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("slow-session", "test", time.time()),
        )

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db._execute_write(insert_session)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "SessionDB write latency" in messages
    assert "caller=insert_session" in messages
    assert "operation=insert_session" in messages
    assert "lock_wait=" in messages
    assert "txn=" in messages
    db.close()


def test_execute_write_attributes_lock_wait_to_observed_owner(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0
    holder_entered = threading.Event()
    release_holder = threading.Event()

    def hold_writer(conn):
        holder_entered.set()
        assert release_holder.wait(timeout=5)

    def wait_for_writer(conn):
        conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("waiter", "test", time.time()),
        )

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(
                db._execute_write,
                hold_writer,
                operation="hold_writer",
            )
            assert holder_entered.wait(timeout=2)
            waiter = pool.submit(
                db._execute_write,
                wait_for_writer,
                operation="wait_for_writer",
            )
            time.sleep(0.05)
            release_holder.set()
            holder.result(timeout=2)
            waiter.result(timeout=2)

    messages = [record.getMessage() for record in caplog.records]
    waiter_log = next(
        message for message in messages if "operation=wait_for_writer" in message
    )
    assert "instance_queue_depth=1" in waiter_log
    assert "instance_owner_operation=hold_writer" in waiter_log
    assert "instance_owner_age_s=" in waiter_log
    db.close()


def test_execute_write_infers_enclosing_session_operation(tmp_path, caplog):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="s1", source="test")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db.set_expiry_finalized("s1")

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "caller=_do" in messages
    assert "operation=set_expiry_finalized" in messages
    db.close()


def test_append_message_slow_write_logs_role_and_byte_attribution(tmp_path, caplog):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="s1", source="test")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    content = "tool output ✓"
    tool_name = "terminal"
    tool_calls = [{"id": "call-1"}]
    reasoning = "not indexed"

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db.append_message(
            session_id="s1",
            role="tool",
            content=content,
            tool_name=tool_name,
            tool_calls=tool_calls,
            reasoning=reasoning,
        )

    expected_fts_bytes = sum(
        len(value.encode("utf-8"))
        for value in (content, tool_name, json.dumps(tool_calls))
    )
    expected_payload_bytes = expected_fts_bytes + len(reasoning.encode("utf-8"))
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "operation=append_message" in messages
    assert "role=tool" in messages
    assert f"payload_bytes={expected_payload_bytes}" in messages
    assert f"fts_bytes={expected_fts_bytes}" in messages
    db.close()


def test_append_message_non_tool_counts_both_fts_indexes(tmp_path, caplog):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="s1", source="test")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db.append_message(session_id="s1", role="user", content="é")

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "payload_bytes=2" in messages
    assert "fts_bytes=4" in messages
    db.close()


def test_append_message_counts_raw_bytes_and_sanitizes_role_label(tmp_path, caplog):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="s1", source="test")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db.append_message(
            session_id="s1",
            role="tool\nforged",
            content=b"\x00\xff",  # type: ignore[arg-type] - robustness probe
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "role=other" in messages
    assert "forged" not in messages
    assert "payload_bytes=2" in messages
    assert "fts_bytes=4" in messages
    db.close()


def test_append_message_fts_bytes_follow_runtime_capabilities(tmp_path, caplog):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="s1", source="test")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db._fts_enabled = False
        db.append_message(session_id="s1", role="user", content="é")
        db._fts_enabled = True
        db._trigram_available = False
        db.append_message(session_id="s1", role="user", content="é")

    messages = [record.getMessage() for record in caplog.records]
    append_logs = [message for message in messages if "operation=append_message" in message]
    assert "fts_bytes=0" in append_logs[-2]
    assert "fts_bytes=2" in append_logs[-1]
    db.close()


def test_append_message_payload_bytes_include_finish_reason(tmp_path, caplog):
    db = SessionDB(tmp_path / "state.db")
    db.create_session(session_id="s1", source="test")
    db._SLOW_WRITE_WARN_S = 0.0
    db._SLOW_LOCK_WAIT_WARN_S = 0.0

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db.append_message(
            session_id="s1",
            role="assistant",
            content="ok",
            finish_reason="stop",
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "payload_bytes=6" in messages
    db.close()


def test_slow_checkpoint_log_includes_duration_and_pages(tmp_path, caplog):
    """A slow PASSIVE checkpoint emits operation, duration, and page metrics."""
    db = SessionDB(db_path=tmp_path / "state.db")
    db._SLOW_CHECKPOINT_WARN_S = 0.0
    db._checkpoint_coordinator.stop(timeout=1.0)
    db._checkpoint_coordinator = db._checkpoint_coordinator.__class__(
        db.db_path, db._CHECKPOINT_INTERVAL_S, db._SLOW_CHECKPOINT_WARN_S
    )

    with caplog.at_level(logging.WARNING, logger="hermes_state"):
        db._checkpoint_coordinator._run_checkpoint()

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "SessionDB slow checkpoint" in messages
    assert "operation=wal_checkpoint" in messages
    assert "mode=PASSIVE" in messages
    assert "duration_s=" in messages
    assert "pages_checkpointed=" in messages
    db.close()
