"""Browse reconciliation reports budget exhaustion before a transaction starts."""
import sqlite3

import pytest

import hermes_state_reconcile as reconcile


def test_begin_interrupted_by_deadline_is_a_browse_budget_refusal(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE history (message TEXT)")
        conn.execute("INSERT INTO history VALUES ('retained')")
    before = path.read_bytes()
    budget = reconcile._Budget(3)
    arm = budget.arm

    def expire_when_armed(conn):
        arm(conn)
        monkeypatch.setattr(budget, "_clock", lambda: budget.deadline + 1)

    monkeypatch.setattr(budget, "arm", expire_when_armed)
    monkeypatch.setattr(reconcile, "_BUDGET_PROGRESS_OPS", 1)
    with pytest.raises(reconcile.BrowseBudgetExceeded):
        reconcile._apply_additive_schema(path, budget)
    assert path.read_bytes() == before
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT message FROM history").fetchall() == [("retained",)]
