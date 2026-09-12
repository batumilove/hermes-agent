"""Regression coverage for #88532.

A multiplexed gateway serves every profile from one process.  ``SessionStore``
used to bind a single ``SessionDB`` during ``__init__``, freezing it to the
process's own root home, so a named profile's sessions were physically written
to the root ``state.db`` even though ``_profile_runtime_scope`` had already
redirected ``get_hermes_home()`` for that turn.  The rows carried the correct
``profile_name``, which is why the only visible symptom was the desktop listing
a profile's session under the default bot: the desktop reads
``profiles/<name>/state.db``, which never received the write.

These tests pin the handle to the *active* scope rather than to construction
time.  ``test_write_under_profile_scope_lands_in_profile_store`` is the one
that reproduces the report; it fails against the pre-fix code with the session
row sitting in the root store.
"""

import sqlite3
import threading
import time
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.post_response import FinalResponseHandoff, PostResponseWorker
from gateway.run import GatewayRunner
from gateway.session import SessionSource, SessionStore, build_session_key
from hermes_constants import reset_hermes_home_override, set_hermes_home_override


@pytest.fixture
def multiplex_homes(tmp_path, monkeypatch):
    """A root home plus a named profile home, with HERMES_HOME on the root.

    Mirrors the reported layout: one gateway process launched under the root
    home, serving a ``fitness`` profile whose store lives under
    ``profiles/fitness``.
    """
    import hermes_state

    root = tmp_path / "hermes"
    profile = root / "profiles" / "fitness"
    root.mkdir(parents=True)
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(root))

    # The suite-wide fixture in conftest re-points ``hermes_state.DEFAULT_DB_PATH``
    # at a fake home, which trips the deliberate escape hatch in
    # ``_default_db_path()``: a re-pointed constant wins over everything,
    # including the context-local override.  That is correct for tests that
    # want one fixed DB, but it would pin every lookup here to a single path
    # and make these assertions vacuous.  Restore the import-time snapshot so
    # the hatch is closed and resolution goes through ``get_hermes_home()``,
    # which is what production does.  ``HERMES_HOME`` above still keeps that
    # resolution inside ``tmp_path``, so no real store is ever opened.
    monkeypatch.setattr(
        hermes_state, "DEFAULT_DB_PATH", hermes_state._IMPORT_DEFAULT_DB_PATH
    )
    return root, profile


def _make_store(root: Path) -> SessionStore:
    with patch("gateway.session.SessionStore._ensure_loaded"):
        store = SessionStore(sessions_dir=root / "sessions", config=GatewayConfig())
    store._loaded = True
    return store


def _session_ids(db_path: Path) -> set:
    """Read session ids straight out of a state.db, or empty if absent."""
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT id FROM sessions").fetchall()
    except sqlite3.OperationalError:
        # No sessions table: nothing was ever written here.
        return set()
    finally:
        conn.close()
    return {r[0] for r in rows}


def test_store_uses_root_db_when_no_profile_scope_is_active(multiplex_homes):
    """Single-profile gateways are unaffected: no scope, same path as before."""
    root, _profile = multiplex_homes
    store = _make_store(root)

    assert Path(store._db.db_path) == root / "state.db"


def test_db_handle_follows_the_active_profile_scope(multiplex_homes):
    """The handle is resolved per access, not frozen at construction."""
    root, profile = multiplex_homes
    store = _make_store(root)

    # Constructed outside any scope, exactly as the gateway constructs it.
    assert Path(store._db.db_path) == root / "state.db"

    token = set_hermes_home_override(str(profile))
    try:
        assert Path(store._db.db_path) == profile / "state.db"
    finally:
        reset_hermes_home_override(token)

    # And the scope is restored once the turn's scope exits.
    assert Path(store._db.db_path) == root / "state.db"


def test_write_under_profile_scope_lands_in_profile_store(multiplex_homes):
    """The reported bug: the row must be in the profile's own file.

    This is the assertion the issue makes by hand with ``sqlite3``: the
    session for profile ``fitness`` belongs in ``profiles/fitness/state.db``
    and must NOT be in the root store.
    """
    root, profile = multiplex_homes
    store = _make_store(root)

    token = set_hermes_home_override(str(profile))
    try:
        store._db.create_session("20260817_233028_542fda58", "feishu")
    finally:
        reset_hermes_home_override(token)

    assert _session_ids(profile / "state.db") == {"20260817_233028_542fda58"}
    assert _session_ids(root / "state.db") == set()


def test_handles_are_cached_per_path(multiplex_homes):
    """One handle per profile: no reopen per message, no sharing across profiles."""
    root, profile = multiplex_homes
    store = _make_store(root)

    root_first = store._db
    root_second = store._db
    assert root_first is root_second

    token = set_hermes_home_override(str(profile))
    try:
        profile_first = store._db
        profile_second = store._db
    finally:
        reset_hermes_home_override(token)

    assert profile_first is profile_second
    assert profile_first is not root_first


def test_explicitly_pinned_handle_still_wins(multiplex_homes):
    """``store._db = ...`` remains authoritative for every subsequent read.

    Guardrail rather than a bug reproduction: a large number of existing
    tests install a fake handle or disable the DB this way, and the property
    must not quietly resolve past a deliberate assignment.
    """
    root, profile = multiplex_homes
    store = _make_store(root)

    sentinel = object()
    store._db = sentinel
    token = set_hermes_home_override(str(profile))
    try:
        assert store._db is sentinel
    finally:
        reset_hermes_home_override(token)

    # Disabling the DB (the JSONL-fallback path) must survive scope changes.
    store._db = None
    token = set_hermes_home_override(str(profile))
    try:
        assert store._db is None
    finally:
        reset_hermes_home_override(token)


def test_close_all_db_handles_sweeps_every_profile_handle(multiplex_homes):
    """Teardown must release every cached per-profile handle, not just the
    one the tearing-down task's own scope resolves.

    Follow-up hardening for the per-path cache: ``gateway/run.py``'s
    teardown path closes ``store._db`` (root scope only); the sweep closes
    the rest so secondary profiles' WAL locks are released before a
    ``--replace`` restart reopens their stores.
    """
    root, profile = multiplex_homes
    store = _make_store(root)

    root_db = store._db
    token = set_hermes_home_override(str(profile))
    try:
        profile_db = store._db
    finally:
        reset_hermes_home_override(token)
    assert root_db is not profile_db

    store.close_all_db_handles()

    # Both handles are closed (connection released) and the cache is empty,
    # so the next access opens a fresh handle rather than a dead one.
    assert root_db._conn is None
    assert profile_db._conn is None
    assert store._db_handles == {}
    fresh = store._db
    assert fresh is not root_db
    assert fresh._conn is not None
    fresh.close()


def test_runner_session_db_follows_the_active_profile_scope(multiplex_homes):
    """GatewayRunner._session_db is the same frozen-handle class of bug.

    /resume, /title, /history and session search run inside
    ``_profile_runtime_scope`` on a multiplexed gateway and must read the
    serving profile's state.db.  Exercise the property on a bare runner shell
    (full construction wires adapters and is irrelevant to the seam under
    test).
    """
    import threading

    from gateway.run import GatewayRunner, _SESSION_DB_UNPINNED

    root, profile = multiplex_homes
    runner = object.__new__(GatewayRunner)
    runner._session_db_pinned = _SESSION_DB_UNPINNED
    runner._session_db_handles = {}
    runner._session_db_handles_lock = threading.Lock()

    root_db = runner._session_db
    assert Path(root_db._db.db_path) == root / "state.db"

    token = set_hermes_home_override(str(profile))
    try:
        profile_db = runner._session_db
        assert Path(profile_db._db.db_path) == profile / "state.db"
        # Cached per path: same wrapper identity on re-access.
        assert runner._session_db is profile_db
    finally:
        reset_hermes_home_override(token)

    assert runner._session_db is root_db

    # Pinning (how suites install fakes / disable the DB) wins across scopes.
    runner._session_db = None
    token = set_hermes_home_override(str(profile))
    try:
        assert runner._session_db is None
    finally:
        reset_hermes_home_override(token)
    runner._session_db_pinned = _SESSION_DB_UNPINNED

    runner.close_all_session_db_handles()
    assert runner._session_db_handles == {}
    assert root_db._db._conn is None
    assert profile_db._db._conn is None


def _post_response_source() -> SessionSource:
    return SessionSource(
        platform=Platform.SLACK,
        chat_id="C-multiplex-post-response",
        chat_type="channel",
        thread_id="T-multiplex-post-response",
        user_id="U-multiplex-post-response",
    )


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _WorkerRunner:
    """Small weak-referenceable runner shell for worker scheduling tests."""

    def __init__(self, store):
        self.session_store = store
        self._clear_restart_failure_count_sync = lambda _key: None
        self._finalize_post_response_turn = lambda _payload: None


def _seed_delivered_profile_barrier(db, entry, *, obligation_id: str) -> None:
    db.create_session("profile-post-response-child", "gateway")
    db.create_final_delivery_and_barrier(
        obligation_id=obligation_id,
        session_key=entry.session_key,
        session_lineage=entry.session_id,
        platform="slack",
        chat_id="C-multiplex-post-response",
        thread_id="T-multiplex-post-response",
        content="profile final",
        actions=[
            {
                "action_kind": "rebind_session_id",
                "payload": {
                    "session_key": entry.session_key,
                    "expected_session_id": entry.session_id,
                    "new_session_id": "profile-post-response-child",
                },
            },
            {
                "action_kind": "append_to_transcript",
                "payload": {
                    "session_id": "profile-post-response-child",
                    "message": {"role": "assistant", "content": "profile final"},
                },
            },
            {
                "action_kind": "update_session",
                "payload": {"session_key": entry.session_key, "last_prompt_tokens": 23},
            },
        ],
    )
    db._execute_write(
        lambda conn: conn.execute(
            "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id=?",
            (obligation_id,),
        )
    )


def test_final_handoff_captures_active_profile_db_and_commits_only_there(multiplex_homes):
    """A handoff remains bound to its turn DB after the turn scope has exited."""
    root, profile = multiplex_homes
    store = _make_store(root)
    runner = object.__new__(GatewayRunner)
    runner.session_store = store
    registered = []
    runner._post_response_controller = SimpleNamespace(
        register_db_path=lambda path: registered.append(Path(path)), wake=lambda: None
    )
    source = _post_response_source()

    token = set_hermes_home_override(str(profile))
    try:
        entry = store.get_or_create_session(source)
        handoff = FinalResponseHandoff(
            "profile final",
            runner=runner,
            session_key=entry.session_key,
            session_lineage=entry.session_id,
            expected_session_id=entry.session_id,
            new_session_id=None,
            active_turn_token=None,
            last_prompt_tokens=0,
            touch_activity=True,
            append_final_text=True,
            clear_resume_pending=False,
        )
    finally:
        reset_hermes_home_override(token)

    assert handoff.post_response_db_path == profile / "state.db"
    assert runner._prepare_final_response_handoff(
        handoff,
        text_content="profile final",
        event=SimpleNamespace(source=source, message_id="profile-handoff"),
    )
    assert registered == [profile / "state.db"]
    assert _db_counts(profile / "state.db") == (1, 3)
    assert _db_counts(root / "state.db") == (0, 0)
    store.close_all_db_handles()


def _db_counts(db_path: Path) -> tuple[int, int]:
    if not db_path.exists():
        return (0, 0)
    conn = sqlite3.connect(str(db_path))
    try:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("delivery_obligations", "gateway_post_response_actions")
        )
    finally:
        conn.close()


def test_worker_rebinds_transcript_and_updates_only_receipt_profile_db(multiplex_homes):
    """Worker claim, dispatch and completion must share the receipt's profile scope."""
    root, profile = multiplex_homes
    source = _post_response_source()
    store = _make_store(root)
    runner = object.__new__(GatewayRunner)
    runner.session_store = store
    runner._clear_restart_failure_count_sync = lambda _key: None
    runner._finalize_post_response_turn = lambda _payload: None

    token = set_hermes_home_override(str(profile))
    try:
        entry = store.get_or_create_session(source)
        _seed_delivered_profile_barrier(store._db, entry, obligation_id="profile-only")
    finally:
        reset_hermes_home_override(token)

    # Same routing key in root is deliberately unrelated to the profile receipt.
    root_db = store._db
    root_db.create_session("root-same-key", "gateway")
    worker = PostResponseWorker(runner)
    try:
        assert worker.register_db_path(profile / "state.db")
        assert profile / "state.db" in worker._db_paths
        worker.start()
        assert _wait_until(
            lambda: not _profile_barrier_exists(profile / "state.db", "profile-only")
        )
        assert store.load_transcript("profile-post-response-child") == []
        token = set_hermes_home_override(str(profile))
        try:
            assert [m["content"] for m in store.load_transcript("profile-post-response-child")] == [
                "profile final"
            ]
            assert store.lookup_by_session_key(entry.session_key).session_id == "profile-post-response-child"
        finally:
            reset_hermes_home_override(token)
        assert root_db.get_session("root-same-key") is not None
        assert root_db.get_session("profile-post-response-child") is None
    finally:
        worker.shutdown(2.0)
        store.close_all_db_handles()


def _profile_barrier_exists(db_path: Path, obligation_id: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        return bool(
            conn.execute(
                "SELECT 1 FROM gateway_post_response_actions "
                "WHERE obligation_id=? AND state != 'done'",
                (obligation_id,),
            ).fetchone()
        )
    finally:
        conn.close()


def test_startup_discovers_profile_receipt_with_one_worker_and_ignores_symlink_escape(
    multiplex_homes, tmp_path
):
    """Restart drains valid profile receipts before traffic, never external symlink stores."""
    root, profile = multiplex_homes
    source = _post_response_source()
    config = GatewayConfig(sessions_dir=root / "sessions")
    old = _make_store(root)
    token = set_hermes_home_override(str(profile))
    try:
        entry = old.get_or_create_session(source)
        _seed_delivered_profile_barrier(old._db, entry, obligation_id="startup-profile")
    finally:
        reset_hermes_home_override(token)
        old.close_all_db_handles()

    outside = tmp_path / "outside-profile"
    outside.mkdir()
    escaped_db = outside / "state.db"
    # A real state DB under the symlink makes this a meaningful escape check.
    escaped = _make_store(root)
    token = set_hermes_home_override(str(outside))
    try:
        escaped._db.create_final_delivery_and_barrier(
            obligation_id="escaped-profile",
            session_key=build_session_key(source),
            session_lineage="escaped",
            platform="slack",
            chat_id="C-multiplex-post-response",
            thread_id="T-multiplex-post-response",
            content="outside",
            actions=[
                {
                    "action_kind": "update_session",
                    "payload": {"session_key": build_session_key(source)},
                }
            ],
        )
        escaped._db._execute_write(
            lambda conn: conn.execute(
                "UPDATE delivery_obligations SET state='delivered' WHERE obligation_id='escaped-profile'"
            )
        )
    finally:
        reset_hermes_home_override(token)
    escaped.close_all_db_handles()
    (root / "profiles" / "escape").symlink_to(outside, target_is_directory=True)

    runner = GatewayRunner(config)
    try:
        assert _wait_until(
            lambda: not _profile_barrier_exists(profile / "state.db", "startup-profile")
        ), "startup must replay the profile receipt before a later profile turn"
        workers = [
            thread for thread in threading.enumerate()
            if thread.name == "gateway-post-response" and thread.is_alive()
        ]
        assert workers == [runner._post_response_worker]
        assert _profile_barrier_exists(escaped_db, "escaped-profile")
    finally:
        runner.shutdown_post_response_worker(timeout=2.0)
        runner.session_store.close_all_db_handles()


def test_worker_rejects_profile_db_when_profiles_root_is_a_symlink(
    multiplex_homes, tmp_path
):
    """The profiles directory itself is part of the trusted containment boundary."""
    root, _profile = multiplex_homes
    store = _make_store(root)
    outside_profiles = tmp_path / "outside-profiles"
    outside_profile = outside_profiles / "escape"
    outside_profile.mkdir(parents=True)
    (outside_profile / "state.db").touch()
    profiles_root = root / "profiles"
    (profiles_root / "fitness").rmdir()
    profiles_root.rmdir()
    profiles_root.symlink_to(outside_profiles, target_is_directory=True)

    runner = _WorkerRunner(store)
    worker = PostResponseWorker(runner)
    try:
        assert not worker.register_db_path(profiles_root / "escape" / "state.db")
        assert profiles_root / "escape" / "state.db" not in worker._db_paths
    finally:
        store.close_all_db_handles()


def test_worker_capacity_evicts_oldest_profile_without_displacing_root(multiplex_homes):
    """A full bounded set stays schedulable and leaves durable rows discoverable."""
    root, profile_one = multiplex_homes
    profile_two = root / "profiles" / "work"
    profile_two.mkdir()
    store = _make_store(root)
    runner = _WorkerRunner(store)
    worker = PostResponseWorker(runner)
    profile_one_db = profile_one / "state.db"
    profile_two_db = profile_two / "state.db"
    profile_one_db.touch()
    profile_two_db.touch()
    worker._MAX_DB_PATHS = 2
    try:
        assert worker.register_db_path(profile_one_db)
        assert worker.register_db_path(profile_two_db)
        assert list(worker._db_paths) == [worker._root_db_path, profile_two_db]
        assert profile_one_db.exists(), "eviction must not discard the durable receipt DB"
    finally:
        store.close_all_db_handles()


def test_worker_wake_fairly_drains_two_registered_profile_databases(multiplex_homes):
    """One wake must not leave a ready profile behind another profile's work."""
    root, profile_one = multiplex_homes
    profile_two = root / "profiles" / "work"
    profile_two.mkdir()
    source = _post_response_source()
    store = _make_store(root)
    runner = _WorkerRunner(store)
    entries = []
    for profile, obligation_id in (
        (profile_one, "wake-profile-one"),
        (profile_two, "wake-profile-two"),
    ):
        token = set_hermes_home_override(str(profile))
        try:
            entry = store.get_or_create_session(source)
            _seed_delivered_profile_barrier(store._db, entry, obligation_id=obligation_id)
            entries.append((profile, obligation_id))
        finally:
            reset_hermes_home_override(token)

    worker = PostResponseWorker(runner)
    try:
        assert worker.register_db_path(profile_one / "state.db")
        assert worker.register_db_path(profile_two / "state.db")
        worker.start()
        worker.wake()
        assert _wait_until(
            lambda: all(not _profile_barrier_exists(profile / "state.db", obligation_id)
                        for profile, obligation_id in entries)
        ), "a wake must drain ready work from both registered profile databases"
    finally:
        worker.shutdown(2.0)
        store.close_all_db_handles()


def test_worker_idle_cycles_do_not_rescan_profiles_at_poll_frequency(
    multiplex_homes, monkeypatch
):
    """Idle polling must not repeatedly list/stat profiles at the old 20Hz cadence."""
    root, _profile = multiplex_homes
    store = _make_store(root)
    runner = _WorkerRunner(store)
    worker = PostResponseWorker(runner)
    profiles_root = root / "profiles"
    original_iterdir = Path.iterdir
    original_lstat = os.lstat
    observed = {"iterdir": 0, "lstat": 0}

    def counted_iterdir(path):
        if path == profiles_root:
            observed["iterdir"] += 1
        return original_iterdir(path)

    def counted_lstat(path, *args, **kwargs):
        if Path(path) == profiles_root:
            observed["lstat"] += 1
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "iterdir", counted_iterdir)
    monkeypatch.setattr("gateway.post_response.os.lstat", counted_lstat)
    try:
        worker.start()
        time.sleep(0.30)
    finally:
        worker.shutdown(2.0)
        store.close_all_db_handles()

    assert observed["iterdir"] <= 1
    assert observed["lstat"] <= 1


def test_restart_failure_cleanup_resolves_profile_home_at_worker_execution_time(
    multiplex_homes,
):
    """A profile finalizer cannot clear an identical root session's restart state."""
    root, profile = multiplex_homes
    session_key = "slack:channel:C-multiplex-post-response:T-multiplex-post-response"
    root_path = root / GatewayRunner._STUCK_LOOP_FILE
    profile_path = profile / GatewayRunner._STUCK_LOOP_FILE
    root_path.write_text(json.dumps({session_key: 1}), encoding="utf-8")
    profile_path.write_text(json.dumps({session_key: 2}), encoding="utf-8")
    runner = object.__new__(GatewayRunner)

    token = set_hermes_home_override(str(profile))
    try:
        runner._clear_restart_failure_count_sync(session_key)
    finally:
        reset_hermes_home_override(token)

    assert json.loads(root_path.read_text(encoding="utf-8")) == {session_key: 1}
    assert not profile_path.exists()
