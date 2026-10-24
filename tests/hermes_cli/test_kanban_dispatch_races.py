"""Fork dispatch PID ownership and claim-residue contracts."""
import json
import sqlite3
from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_dispatch as kbd, kanban_db_workspace as kbw
from tests.hermes_cli import test_kanban_db as _kanban_db_tests

kanban_home = _kanban_db_tests.kanban_home

def _row(conn, task_id: str) -> dict:
    r = conn.execute(
        "SELECT status, claim_lock, worker_pid FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    return dict(r)

def test_dispatch_once_clears_claim_and_pid_on_spawn_failure(kanban_home, monkeypatch):
    """Round-6 note: a tick whose spawn raises must leave the task re-queueable —
    claim_lock and worker_pid NULL, status back to ready (or blocked once the
    failure limit is exhausted — still claim-residue-free). A stale non-NULL
    claim_lock here wedges every later reclaim (guarded CAS compares against
    it), so this is an invariant, not a snapshot. Memory pressure is pinned so
    the spawn path is guaranteed to run (not vacuously skipped)."""
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "ok")
    calls = []
    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="failing spawn", assignee="default")

        def boom(task, workspace, board=None):
            calls.append(task.id)
            raise RuntimeError("simulated spawn failure")

        # default failure_limit: first failure requeues rather than blocks
        res = kbd.dispatch_once(conn, spawn_fn=boom)
        row = _row(conn, tid)
        later = kb.get_task(conn, tid)

    assert calls == [tid], "spawn path must actually run"
    assert not res.spawned
    # invariant: no claim residue after a failed tick
    assert row["claim_lock"] is None, row
    assert row["worker_pid"] is None, row
    assert later is not None and later.status in ("ready", "triage", "blocked")

def test_dispatch_once_quiesced_tick_leaves_no_claim_residue(kanban_home, monkeypatch):
    """Round-6 note: after dispatch_once completes and any spawned worker
    finishes, every task row must be claim-residue-free: claim_lock IS NULL
    and worker_pid IS NULL wherever status is not 'running'. Memory pressure
    is pinned and the running-time row is observed mid-spawn so the test
    cannot pass vacuously; tmux cleanup is stubbed to keep it hermetic (the
    DB completion transition stays real)."""
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "ok")
    monkeypatch.setattr(kbw, "_cleanup_worker_tmux", lambda *a, **k: None)
    spawns = []
    running_rows = {}

    def fake_spawn(task, workspace, board=None):
        spawns.append(task.id)
        # observe the in-flight row exactly as the dispatcher holds it
        with kbc.connect_closing() as peek:
            running_rows[task.id] = _row(peek, task.id)
        return 4242

    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="quiesced", assignee="default")
        res = kbd.dispatch_once(conn, spawn_fn=fake_spawn)
        assert spawns == [tid], "spawn path must actually run"
        assert res.spawned, "dispatch must report the spawn"
        mid = running_rows[tid]
        assert mid["status"] == "running", mid
        # simulate worker completion on the real DB transition path
        kb.complete_task(conn, tid, result="ok")
        for r in conn.execute(
            "SELECT id, status, claim_lock, worker_pid FROM tasks"
        ).fetchall():
            if r["status"] != "running":
                assert r["claim_lock"] is None, dict(r)
                assert r["worker_pid"] is None, dict(r)

def _set_task_status(conn: sqlite3.Connection, task_id: str, status: str) -> None:
    """Test helper: set a task's status directly."""
    conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))

def test_fast_worker_completion_before_pid_persist_leaves_no_stale_pid(kanban_home, monkeypatch):
    """Round-3 deferred production fix: a worker that completes through another
    connection between spawn_fn returning and _set_worker_pid persisting must
    not end up with a stale worker_pid on a done task. The unconditional
    UPDATE in _set_worker_pid is the bug; the fix guards it on status."""
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "ok")
    monkeypatch.setattr(kbw, "_cleanup_worker_tmux", lambda *a, **k: None)
    spawned = []
    spawned_hooks: list[dict] = []
    from hermes_cli.plugins import get_plugin_manager
    _mgr = get_plugin_manager()
    monkeypatch.setattr(
        _mgr,
        "_hooks",
        {name: list(callbacks) for name, callbacks in _mgr._hooks.items()},
    )
    hook = lambda **kw: spawned_hooks.append(kw)
    hooks = _mgr._hooks.setdefault("on_kanban_worker_spawned", [])
    hooks.append(hook)

    def fast_worker(task, workspace, board=None):
        # complete the task from a second connection BEFORE the dispatcher
        # persists the pid — the exact race window.
        spawned.append(task.id)
        with kbc.connect_closing() as other:
            kb.complete_task(other, task.id, result="done fast")
        return 4242

    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="fast worker", assignee="default")
        res = kbd.dispatch_once(conn, spawn_fn=fast_worker)

        assert spawned == [tid], "spawn path must actually run"
        assert res.spawned
        row = _row(conn, tid)
        assert row["status"] == "done", row
        # THE INVARIANT: no stale pid residue on a completed task
        assert row["worker_pid"] is None, row
        assert row["claim_lock"] is None, row
        # the ended run keeps no pid either
        run = conn.execute(
            "SELECT worker_pid FROM task_runs WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (tid,),
        ).fetchone()
        if run is not None:
            assert run["worker_pid"] is None, dict(run)
        # and NO "spawned" event exists for this task at all (the pid
        # never durably landed, so it must never be announced)
        n_spawned = conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id = ? AND kind = 'spawned'",
            (tid,),
        ).fetchone()[0]
        assert n_spawned == 0
        # and the RFC #58548 lifecycle hook must NOT have fired for a pid
        # the board refused
        assert spawned_hooks == [], spawned_hooks

def test_set_worker_pid_loses_aba_race_to_replacement_run(kanban_home, monkeypatch):
    """ABA guard: if the claimed run ended and the task was reclaimed into a
    NEW run before the old dispatcher persisted its pid, the pid must not
    land on the replacement attempt (nor emit a spawned event for it)."""
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "ok")
    monkeypatch.setattr(kbw, "_cleanup_worker_tmux", lambda *a, **k: None)
    holder = {}
    aba_hooks: list[dict] = []
    from hermes_cli.plugins import get_plugin_manager
    _mgr = get_plugin_manager()
    monkeypatch.setattr(
        _mgr,
        "_hooks",
        {name: list(callbacks) for name, callbacks in _mgr._hooks.items()},
    )
    _mgr._hooks.setdefault("on_kanban_worker_spawned", []).append(
        lambda **kw: aba_hooks.append(kw)
    )

    def racing_worker(task, workspace, board=None):
        holder["old_run"] = task.current_run_id
        # end our run and start a replacement run (as a reclaiming
        # dispatcher would), all from a second connection BEFORE the
        # pid persist
        import time as _time
        with kbc.connect_closing() as other:
            kb.complete_task(other, task.id, result="old done")
            with kb.write_txn(other):
                now = int(_time.time())
                cur = other.execute(
                    "INSERT INTO task_runs (task_id, status, started_at) "
                    "VALUES (?, 'running', ?)",
                    (task.id, now),
                )
                new_run = cur.lastrowid
                # the replacement dispatcher has ALREADY persisted its own
                # pid — this sentinel must survive the old dispatcher's
                # losing _set_worker_pid
                other.execute(
                    "UPDATE tasks SET status='running', current_run_id=?, "
                    "worker_pid=7777 WHERE id = ?",
                    (new_run, task.id),
                )
                other.execute(
                    "UPDATE task_runs SET worker_pid=7777 WHERE id = ?",
                    (new_run,),
                )
        return 5555

    with kbc.connect_closing() as conn:
        tid = kb.create_task(conn, title="aba", assignee="default")
        res = kbd.dispatch_once(conn, spawn_fn=racing_worker)
        assert res.spawned
        row = _row(conn, tid)
        # the replacement dispatcher's pid must SURVIVE untouched —
        # neither overwritten with 5555 nor erased to NULL
        assert row["worker_pid"] == 7777, row
        repl_run = conn.execute(
            "SELECT worker_pid FROM task_runs WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (tid,),
        ).fetchone()
        assert repl_run is not None and repl_run["worker_pid"] == 7777, dict(repl_run)
        # no spawned event for the replacement run from the OLD dispatcher
        n_spawned = conn.execute(
            "SELECT COUNT(*) FROM task_events "
            "WHERE task_id = ? AND kind = 'spawned' AND payload LIKE '%5555%'",
            (tid,),
        ).fetchone()[0]
        assert n_spawned == 0
        # and the lifecycle hook must not have fired for the stale pid
        assert aba_hooks == [], aba_hooks

def test_set_worker_pid_success_persists_rows_event_and_hook(kanban_home, monkeypatch):
    """A matching running run persists one pid and announces it exactly once."""
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "ok")
    monkeypatch.setattr(kbw, "_cleanup_worker_tmux", lambda *a, **k: None)

    from hermes_cli.plugins import get_plugin_manager

    spawned_hooks: list[dict] = []
    mgr = get_plugin_manager()
    monkeypatch.setattr(
        mgr,
        "_hooks",
        {name: list(callbacks) for name, callbacks in mgr._hooks.items()},
    )
    hooks = mgr._hooks.setdefault("on_kanban_worker_spawned", [])
    hook = lambda **kw: spawned_hooks.append(kw)
    hooks.append(hook)

    with kbc.connect_closing() as conn:
            tid = kb.create_task(conn, title="pid success", assignee="default")
            res = kbd.dispatch_once(conn, spawn_fn=lambda *args, **kwargs: 4242)

            assert res.spawned
            row = conn.execute(
                "SELECT status, worker_pid, current_run_id FROM tasks WHERE id = ?",
                (tid,),
            ).fetchone()
            assert row is not None
            assert row["status"] == "running", dict(row)
            assert row["worker_pid"] == 4242, dict(row)
            run_id = row["current_run_id"]
            assert run_id is not None

            run = conn.execute(
                "SELECT worker_pid FROM task_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            assert run is not None and run["worker_pid"] == 4242, dict(run)

            events = conn.execute(
                "SELECT run_id, payload FROM task_events "
                "WHERE task_id = ? AND kind = 'spawned'",
                (tid,),
            ).fetchall()
            assert len(events) == 1
            assert events[0]["run_id"] == run_id
            payload = json.loads(events[0]["payload"])
            assert payload["pid"] == 4242

            assert len(spawned_hooks) == 1
            hook_payload = spawned_hooks[0]
            assert hook_payload["task_id"] == tid
            assert hook_payload["run_id"] == run_id
            assert hook_payload["worker_pid"] == 4242
            assert hook_payload["board"] == "default"

def test_no_pid_spawn_hook_keeps_claimed_run_id_across_replacement(kanban_home, monkeypatch):
    """A pid-less spawn hook belongs to the run claimed before spawn_fn."""
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "ok")
    observed: list[dict] = []
    from hermes_cli.plugins import get_plugin_manager
    manager = get_plugin_manager()
    monkeypatch.setattr(
        manager,
        "_hooks",
        {name: list(callbacks) for name, callbacks in manager._hooks.items()},
    )
    manager._hooks.setdefault("on_kanban_worker_spawned", []).append(
        lambda **kwargs: observed.append(kwargs)
    )
    run_ids: dict[str, int] = {}

    def pidless_replacement(task, workspace, board=None):
        assert task.current_run_id is not None
        run_ids["claimed"] = task.current_run_id
        import time as _time
        with kbc.connect_closing() as other:
            kb.complete_task(other, task.id, result="old done")
            with kb.write_txn(other):
                cursor = other.execute(
                    "INSERT INTO task_runs (task_id, status, started_at) "
                    "VALUES (?, 'running', ?)",
                    (task.id, int(_time.time())),
                )
                assert cursor.lastrowid is not None
                run_ids["replacement"] = cursor.lastrowid
                other.execute(
                    "UPDATE tasks SET status='running', current_run_id=? WHERE id=?",
                    (cursor.lastrowid, task.id),
                )

    with kbc.connect_closing() as conn:
        kb.create_task(conn, title="pidless attribution", assignee="default")
        result = kbd.dispatch_once(conn, spawn_fn=pidless_replacement)

    assert result.spawned
    assert len(observed) == 1
    assert run_ids["claimed"] != run_ids["replacement"]
    assert observed[0]["run_id"] == run_ids["claimed"], observed[0]

def test_review_pidless_spawn_hook_keeps_claimed_run_id_across_replacement(
    kanban_home, monkeypatch,
):
    """Review dispatch keeps claim-time attribution for a pid-less spawn."""
    monkeypatch.setattr(kbd, "_memory_pressure_level", lambda: "ok")
    observed: list[dict] = []
    from hermes_cli.plugins import get_plugin_manager
    manager = get_plugin_manager()
    monkeypatch.setattr(
        manager,
        "_hooks",
        {name: list(callbacks) for name, callbacks in manager._hooks.items()},
    )
    manager._hooks.setdefault("on_kanban_worker_spawned", []).append(
        lambda **kwargs: observed.append(kwargs)
    )
    run_ids: dict[str, int] = {}

    def pidless_replacement(task, workspace, board=None):
        assert task.current_run_id is not None
        run_ids["claimed"] = task.current_run_id
        import time as _time
        with kbc.connect_closing() as other:
            kb.complete_task(other, task.id, result="review old done")
            with kb.write_txn(other):
                cursor = other.execute(
                    "INSERT INTO task_runs (task_id, status, started_at) "
                    "VALUES (?, 'running', ?)",
                    (task.id, int(_time.time())),
                )
                assert cursor.lastrowid is not None
                run_ids["replacement"] = cursor.lastrowid
                other.execute(
                    "UPDATE tasks SET status='running', current_run_id=? WHERE id=?",
                    (cursor.lastrowid, task.id),
                )

    with kbc.connect_closing() as conn:
        task_id = kb.create_task(conn, title="review pidless attribution", assignee="default")
        _set_task_status(conn, task_id, "review")
        result = kbd.dispatch_once(conn, spawn_fn=pidless_replacement)

    assert result.spawned
    assert len(observed) == 1
    assert run_ids["claimed"] != run_ids["replacement"]
    assert observed[0]["run_id"] == run_ids["claimed"], observed[0]
