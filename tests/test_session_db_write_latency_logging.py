import logging
import time

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
    assert "lock_wait=" in messages
    assert "txn=" in messages
    db.close()
