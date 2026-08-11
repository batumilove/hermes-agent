"""Concurrency contracts for the SessionDB recall reader."""

import threading

from hermes_state import SessionDB


def test_session_search_dedicated_reader_does_not_convoy_writer(tmp_path):
    """A paused FTS read must not hold the writer connection's Python lock."""
    db = SessionDB(tmp_path / "state.db")
    release_search = threading.Event()
    search_started = threading.Event()
    writer_done = threading.Event()
    search_rows = []
    search_errors = []
    writer_errors = []

    try:
        db.create_session("recall-session", source="cli", model="test")
        for index in range(200):
            db.append_message(
                "recall-session",
                role="user",
                content=f"dedicated recall reader marker {index}",
            )

        # The production runtime uses the split reader when WAL is available.
        # Force that capability here because the test runner may deliberately
        # downgrade vulnerable system SQLite builds to DELETE journaling.
        with db._lock:
            assert db._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
            db._wal_active = True
        assert db.search_messages('"dedicated recall reader"', limit=5)

        def pause_search() -> int:
            search_started.set()
            release_search.wait(timeout=5)
            return 0

        def run_search() -> None:
            try:
                # Borrow through the bounded pool contract.  Calling
                # _get_read_conn() directly no longer reserves that connection
                # for the current thread; _read_ctx() owns checkout and return.
                with db._read_ctx() as conn:
                    assert conn is not None
                    conn.set_progress_handler(pause_search, 1)
                    try:
                        search_rows.extend(
                            conn.execute(
                                "SELECT rowid FROM messages_fts "
                                "WHERE messages_fts MATCH ? LIMIT 20",
                                ('"dedicated recall reader"',),
                            ).fetchall()
                        )
                    finally:
                        conn.set_progress_handler(None, 0)
            except BaseException as exc:
                search_errors.append(exc)

        def run_writer() -> None:
            try:
                db.update_token_counts(
                    "recall-session", input_tokens=1, output_tokens=1
                )
            except BaseException as exc:  # surfaced in the main test thread
                writer_errors.append(exc)
            finally:
                writer_done.set()

        search_thread = threading.Thread(target=run_search)
        search_thread.start()
        assert search_started.wait(timeout=2), "FTS query did not enter SQLite"

        writer_thread = threading.Thread(target=run_writer)
        writer_thread.start()
        assert writer_done.wait(timeout=1), (
            "writer was convoyed behind the paused recall query"
        )
        assert writer_errors == []

        release_search.set()
        search_thread.join(timeout=5)
        writer_thread.join(timeout=5)
        assert not search_thread.is_alive()
        assert not writer_thread.is_alive()
        assert search_errors == []
        assert search_rows
    finally:
        release_search.set()
        db.close()
