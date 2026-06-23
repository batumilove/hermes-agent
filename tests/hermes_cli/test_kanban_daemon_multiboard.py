"""Regression tests for multi-board standalone Kanban daemon dispatch.

The gateway-embedded dispatcher iterates all boards, but the standalone daemon
historically called dispatch_once() only on the current/default board. External
dispatcher deployments therefore left tasks on named boards permanently ready.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(home))
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_BOARD", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db(board="default")
    return home


def test_run_daemon_dispatches_ready_tasks_on_named_boards(kanban_home, monkeypatch):
    (kanban_home / "profiles" / "worker").mkdir(parents=True, exist_ok=True)
    board = "external-e2e"
    kb.create_board(board, name="External E2E")
    with kb.connect(board=board) as conn:
        task_id = kb.create_task(conn, title="named board task", assignee="worker")

    spawned = []

    def fake_spawn(task, workspace_path, board=None):
        spawned.append((task.id, board))
        return 424242

    monkeypatch.setattr(kb, "_default_spawn", fake_spawn)

    stop = threading.Event()
    ticks = []

    def on_tick(result):
        ticks.append(result)
        if spawned:
            stop.set()

    runner = threading.Thread(
        target=lambda: kb.run_daemon(interval=0.05, stop_event=stop, on_tick=on_tick),
        daemon=True,
    )
    runner.start()
    deadline = time.time() + 2.0
    while time.time() < deadline and not spawned:
        time.sleep(0.05)
    stop.set()
    runner.join(timeout=2.0)

    assert not runner.is_alive()
    assert (task_id, board) in spawned
    assert ticks, "daemon should have produced at least one tick result"

    with kb.connect(board=board) as conn:
        task = kb.get_task(conn, task_id)
    assert task is not None
    assert task.status == "running"
    assert task.worker_pid == 424242
