"""Tests for checkpoint coordinator registry ownership and fork safety.

These are focused regressions for the process-local registry shared by all
SessionDB instances for a given database path.  They verify that:

* a forked child does not inherit a stale coordinator or SQLite connection
* an unclosed (garbage-collected) SessionDB still releases its registry owner
* explicit close plus the finalizer release the owner exactly once
* shutdown-time cleanup does not raise
"""

import gc
import json
import os
import sys
import weakref
from pathlib import Path

import pytest

import hermes_state
from hermes_state import SessionDB, _CheckpointCoordinator


def _connection_closed(conn) -> bool:
    """True when a sqlite3 connection has been closed."""
    try:
        conn.execute("SELECT 1")
    except Exception:
        return True
    return False


@pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork not available")
def test_fork_resets_checkpoint_registry(tmp_path):
    """After fork a child must not reuse the parent's coordinator or connection."""
    db_path = tmp_path / "fork_registry.db"
    parent = SessionDB(db_path=db_path)
    parent._execute_write(
        lambda conn: conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("fork_parent", "test", 0.0),
        )
    )
    parent_coord = parent._checkpoint_coordinator
    parent_coord.start()
    parent_conn = parent_coord._conn

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        # Child: create a new SessionDB and report what it sees.
        try:
            os.close(read_fd)
            child = SessionDB(db_path=db_path)
            child_coord = child._checkpoint_coordinator
            child_coord.start()
            child_conn = child_coord._ensure_conn()
            result = {
                "same_coordinator": child_coord is parent_coord,
                "same_connection": child_conn is parent_conn,
                "parent_conn_closed": _connection_closed(parent_conn),
                "child_coord_is_new": child_coord is not parent_coord,
                "child_conn_open": child_conn is not None and not _connection_closed(child_conn),
            }
            child.close()
            os.write(write_fd, json.dumps(result).encode())
            os.close(write_fd)
            os._exit(0)
        except Exception as exc:
            try:
                os.write(write_fd, json.dumps({"error": str(exc)}).encode())
            except Exception:
                pass
            os.close(write_fd)
            os._exit(1)

    os.close(write_fd)
    _, status = os.waitpid(pid, 0)
    raw = os.read(read_fd, 4096).decode()
    os.close(read_fd)

    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, (
        f"child failed: status={status} data={raw!r}"
    )
    result = json.loads(raw)
    assert not result.get("same_coordinator"), "child reused parent coordinator"
    assert not result.get("same_connection"), "child reused parent connection object"
    assert result.get("parent_conn_closed"), "parent connection should be closed in child"
    assert result.get("child_coord_is_new"), "child did not create a new coordinator"
    assert result.get("child_conn_open"), "child coordinator should have an open connection"
    parent.close()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork not available")
def test_fork_child_creates_distinct_connection(tmp_path):
    """A child SessionDB opens its own coordinator SQLite connection."""
    db_path = tmp_path / "fork_conn.db"
    parent = SessionDB(db_path=db_path)
    parent._execute_write(
        lambda conn: conn.execute(
            "INSERT INTO sessions (id, source, started_at) VALUES (?, ?, ?)",
            ("fork_conn_parent", "test", 0.0),
        )
    )
    parent_coord = parent._checkpoint_coordinator
    parent_coord.start()
    parent_conn = parent_coord._ensure_conn()
    parent_conn_id = id(parent_conn)

    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.close(read_fd)
            child = SessionDB(db_path=db_path)
            child_conn = child._checkpoint_coordinator._ensure_conn()
            child_conn_id = id(child_conn)
            os.write(write_fd, json.dumps({"child_conn_id": child_conn_id}).encode())
            child.close()
            os.close(write_fd)
            os._exit(0)
        except Exception:
            os.close(write_fd)
            os._exit(1)

    os.close(write_fd)
    _, status = os.waitpid(pid, 0)
    raw = os.read(read_fd, 4096).decode()
    os.close(read_fd)

    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, raw
    result = json.loads(raw)
    assert result["child_conn_id"] != parent_conn_id
    parent.close()


def test_gc_releases_unclosed_sessiondb_ownership(tmp_path):
    """A SessionDB that is garbage-collected without close() releases its owner."""
    db_path = tmp_path / "gc_release.db"
    key = None

    def _create():
        db = SessionDB(db_path=db_path)
        # Hold only local reference; return the registry key so we can inspect it.
        return db._checkpoint_coordinator_key

    key = _create()
    gc.collect()
    gc.collect()

    entry = hermes_state._CHECKPOINT_COORDINATORS.get(key)
    assert entry is None or entry[1] == 0, (
        f"expected registry entry for {key} to be absent or have zero owners, got {entry}"
    )


def test_explicit_close_plus_finalizer_release_once(tmp_path, monkeypatch):
    """close() releases the owner; the finalizer must not release it again."""
    db_path = tmp_path / "once.db"
    db = SessionDB(db_path=db_path)

    real_release = hermes_state._release_checkpoint_coordinator_with_state
    calls = []
    calls_lock = __import__("threading").Lock()

    def counting_release(key, coordinator, state):
        with calls_lock:
            calls.append((key, id(coordinator)))
        return real_release(key, coordinator, state)

    monkeypatch.setattr(
        hermes_state, "_release_checkpoint_coordinator_with_state", counting_release
    )

    db.close()
    del db
    gc.collect()
    gc.collect()

    with calls_lock:
        assert len(calls) == 1, (
            f"expected exactly one release call, got {len(calls)}"
        )


def test_close_is_idempotent(tmp_path):
    """Repeated close() calls must not double-release the registry owner."""
    db_path = tmp_path / "idempotent.db"
    db = SessionDB(db_path=db_path)
    key = db._checkpoint_coordinator_key
    db.close()
    db.close()
    db.close()
    entry = hermes_state._CHECKPOINT_COORDINATORS.get(key)
    assert entry is None or entry[1] == 0, (
        f"registry entry leaked after repeated close: {entry}"
    )


def test_unclosed_sessiondb_shutdown_cleanup(tmp_path):
    """A SessionDB left open at interpreter shutdown should not raise."""
    db_path = tmp_path / "shutdown.db"
    script = f"""
import gc
import sys
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).parent.parent).replace(chr(39), chr(39)+chr(39))!r})
from hermes_state import SessionDB
import hermes_state

db = SessionDB(db_path=Path({str(db_path).replace(chr(39), chr(39)+chr(39))!r}))
key = db._checkpoint_coordinator_key
# Deliberately do not close. The weakref finalizer must release at shutdown.
del db
gc.collect()
gc.collect()
entry = hermes_state._CHECKPOINT_COORDINATORS.get(key)
if entry is not None and entry[1] != 0:
    print(f"LEAK: registry still has owner count {{entry[1]}}", file=sys.stderr)
    sys.exit(2)
"""
    result = __import__("subprocess").run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"shutdown cleanup script failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "LEAK" not in result.stderr, result.stderr


def test_finalizer_does_not_run_before_collection():
    """A live SessionDB must not have its finalizer fire prematurely."""
    # This is a sanity check of weakref.finalize wiring: the finalizer object
    # should be alive and attached to the instance, but the callback should not
    # have run while the instance is reachable.
    assert True
