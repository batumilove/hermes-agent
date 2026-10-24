"""Actual agent construction serializes response accounting and engine teardown."""
import threading
from types import SimpleNamespace

from hermes_state import SessionDB
from agent.turn_usage import record_response_usage
from tests.agent import compression_fixtures

_fixed_context_window = compression_fixtures._fixed_context_window


def test_constructed_agent_keeps_engine_owned_until_response_accounting_finishes(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("usage-owner", source="cli")
    agent = compression_fixtures._build_agent_with_db(db, "usage-owner")
    entered = threading.Event()
    release = threading.Event()
    shutdown_done = threading.Event()
    order = []
    errors = []

    class Engine:
        def note_usage_less_response(self):
            entered.set()
            assert release.wait(2)
            order.append("usage")

        def shutdown(self):
            order.append("shutdown")
            shutdown_done.set()

    engine = Engine()
    agent.context_compressor = engine
    assert agent._memory_manager_release_guard() is agent._memory_manager_release_lock

    def account():
        try:
            record_response_usage(
                agent, SimpleNamespace(usage=None), messages=[], api_call_count=1,
                api_duration=0.01, compression_attempts=0, max_compression_attempts=3,
            )
        except BaseException as exc:
            errors.append(exc)

    accounting = threading.Thread(target=account)
    teardown = threading.Thread(target=agent._shutdown_owned_context_engine)
    try:
        accounting.start()
        assert entered.wait(2)
        claimed = agent._context_engine_shutdown_lock.acquire(blocking=False)
        if claimed:
            agent._context_engine_shutdown_lock.release()
        assert not claimed, "response accounting must own the actual constructor's engine lock"
        teardown.start()
        assert agent.context_compressor is engine
        assert not shutdown_done.is_set()
    finally:
        release.set()
        accounting.join(timeout=2)
        if teardown.ident is not None:
            teardown.join(timeout=2)
        agent.close()
        db.close()
    assert not accounting.is_alive() and not teardown.is_alive()
    assert not errors
    assert order == ["usage", "shutdown"]
    assert agent.context_compressor is None
