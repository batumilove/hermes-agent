"""Invocation admission and lease cleanup fault contracts."""
import sqlite3
import threading
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("identity", ["task", "session"])
def test_identity_fault_releases_invocation_lock(identity):
    from agent.turn_facade import TurnFacadeMixin

    class BadIdentity:
        def __bool__(self):
            raise ValueError("identity fault")

        def __str__(self):
            raise ValueError("identity fault")

    agent = TurnFacadeMixin()
    agent.session_id = BadIdentity() if identity == "session" else "session"
    with pytest.raises(ValueError, match="identity fault"):
        agent.run_conversation("ask", task_id=BadIdentity() if identity == "task" else "task")
    assert not agent._turn_invocation_lock.locked()


def test_release_database_error_retains_holder(tmp_path):
    from agent.turn_facade_lease import DurableTurnLease

    connection = sqlite3.connect(tmp_path / "lease.db")
    connection.execute("CREATE TABLE leases (session TEXT, holder TEXT)")
    connection.execute("INSERT INTO leases VALUES ('session', 'holder')")
    connection.commit()
    connection.execute("CREATE TRIGGER deny_release BEFORE DELETE ON leases BEGIN SELECT RAISE(ABORT, 'release fault'); END")

    class Store:
        def release_session_turn_lease(self, session, holder):
            with connection:
                connection.execute("DELETE FROM leases WHERE session=? AND holder=?", (session, holder))

    agent = SimpleNamespace(_active_session_turn_lease_holder="holder", _active_session_turn_lease_ttl_seconds=300)
    lease = DurableTurnLease(agent, Store(), "session", "holder")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="release fault"):
            lease.release()
        assert agent._active_session_turn_lease_holder == "holder"
        assert connection.execute("SELECT holder FROM leases").fetchall() == [("holder",)]
        connection.execute("DROP TRIGGER deny_release")
        lease.release()
        assert agent._active_session_turn_lease_holder is None
        assert connection.execute("SELECT holder FROM leases").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize("phase", ["clear_interrupt", "release"])
def test_recovery_failure_retains_quarantine_and_owned_token(phase):
    from agent.turn_facade import TurnFacadeMixin
    from tools.interrupt import is_interrupted, set_interrupt

    agent = TurnFacadeMixin()
    tid = threading.get_ident()
    agent._execution_thread_id = tid
    def fault():
        raise RuntimeError("cleanup fault")
    lease = SimpleNamespace(stop_refresher=lambda: None, join_threads=lambda: True,
                            clear_interrupt=lambda: None, release=lambda: None)
    setattr(lease, phase, fault)
    agent._turn_quarantine = {"lease": lease, "owned_tid": tid}
    set_interrupt(True, tid)
    try:
        with pytest.raises(RuntimeError, match="cleanup fault"):
            agent.recover_quarantined_turn()
        assert agent._turn_quarantine["lease"] is lease
        assert agent._execution_thread_id == tid
        assert is_interrupted()
        assert not agent._turn_invocation_lock.locked()
    finally:
        set_interrupt(False, tid)
