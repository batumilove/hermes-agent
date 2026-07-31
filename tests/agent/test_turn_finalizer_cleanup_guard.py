"""Regression test for #8049.

When the post-loop cleanup chain in ``finalize_turn`` raises — trajectory
save (file I/O), resource teardown (remote VM/browser), or session
persistence (SQLite) — the partial ``final_response`` the caller is waiting
for must still be returned.  Previously any of those raised straight out of
``run_conversation``, so a subprocess wrapper saw an empty stdout with no
traceback and lost the whole turn.
"""

import socket
import threading
import time
from pathlib import Path

import httpx
import pytest

from agent.turn_finalizer import finalize_turn
from run_agent import AIAgent


class _StubBudget:
    used = 5
    max_total = 3
    remaining = 0


class _StubCompressor:
    last_prompt_tokens = 0


class _StubAgent:
    """Minimal agent surface that ``finalize_turn`` reads from."""

    def __init__(self, *, raise_in):
        self._raise_in = set(raise_in)
        self.request_client_close_reasons = []
        self.max_iterations = 3
        self.iteration_budget = _StubBudget()
        self.context_compressor = _StubCompressor()
        self.model = "stub/model"
        self.provider = "stub"
        self.base_url = "http://stub"
        self.session_id = "sess-1"
        self.quiet_mode = True
        self.platform = "cli"
        self._interrupt_requested = False
        self._interrupt_message = None
        self._tool_guardrail_halt_decision = None
        self._response_was_previewed = False
        self._skill_nudge_interval = 0
        self._iters_since_skill = 0
        for attr in (
            "session_input_tokens",
            "session_output_tokens",
            "session_cache_read_tokens",
            "session_cache_write_tokens",
            "session_reasoning_tokens",
            "session_prompt_tokens",
            "session_completion_tokens",
            "session_total_tokens",
            "session_estimated_cost_usd",
        ):
            setattr(self, attr, 0)
        self.session_cost_status = "ok"
        self.session_cost_source = "stub"

    # --- fallible cleanup surfaces -------------------------------------
    def _save_trajectory(self, *a, **k):
        if "save_trajectory" in self._raise_in:
            raise RuntimeError("trajectory disk full")

    def _cleanup_task_resources(self, *a, **k):
        if "cleanup_task_resources" in self._raise_in:
            raise RuntimeError("docker teardown EOF")

    def _drop_trailing_empty_response_scaffolding(self, *a, **k):
        pass

    def _persist_session(self, *a, **k):
        if "persist_session" in self._raise_in:
            raise RuntimeError("sqlite database is locked")

    # --- harmless no-ops ------------------------------------------------
    def _emit_status(self, *a, **k):
        pass

    def _safe_print(self, *a, **k):
        pass

    def _handle_max_iterations(self, messages, n):
        return "PARTIAL SUMMARY FROM MODEL"

    def _file_mutation_verifier_enabled(self):
        return False

    def _turn_completion_explainer_enabled(self):
        return False

    def _drain_pending_steer(self):
        return None

    def clear_interrupt(self):
        pass

    def _sync_external_memory_for_turn(self, **k):
        pass

    def _close_idle_cached_request_openai_client(self, *, reason):
        self.request_client_close_reasons.append(reason)


def _run(
    agent,
    *,
    final_response=None,
    api_call_count=3,
    turn_exit_reason="unknown",
):
    messages = [
        {"role": "user", "content": "do a thing"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "file contents"},
    ]
    return finalize_turn(
        agent,
        final_response=final_response,
        api_call_count=api_call_count,
        interrupted=False,
        failed=False,
        messages=messages,
        conversation_history=None,
        effective_task_id="task-1",
        turn_id="turn-1",
        user_message="do a thing",
        original_user_message="do a thing",
        _should_review_memory=False,
        _turn_exit_reason=turn_exit_reason,
    )




@pytest.mark.parametrize(
    "step", ["save_trajectory", "cleanup_task_resources", "persist_session"]
)
def test_single_cleanup_step_raises_does_not_skip_others(step):
    agent = _StubAgent(raise_in=(step,))
    result = _run(agent)
    # Response survives.
    assert result["final_response"] == "PARTIAL SUMMARY FROM MODEL"
    # Exactly the failing step is recorded; the others ran without error.
    assert result["cleanup_errors"] == [
        next(
            e
            for e in result["cleanup_errors"]
            if e.startswith(step)
        )
    ]
    assert len(result["cleanup_errors"]) == 1


def test_clean_turn_has_no_cleanup_errors_key():
    agent = _StubAgent(raise_in=())
    result = _run(agent)
    assert result["final_response"] == "PARTIAL SUMMARY FROM MODEL"
    assert result["completed"] is False
    assert "cleanup_errors" not in result


def test_turn_wrapper_closes_idle_request_pool_on_early_return(monkeypatch):
    """Even loop returns that bypass finalize_turn must release the pool."""
    agent = AIAgent.__new__(AIAgent)
    reasons = []
    agent._close_idle_cached_request_openai_client = (
        lambda *, reason: reasons.append(reason)
    )
    monkeypatch.setattr(
        "agent.conversation_loop.run_conversation",
        lambda *_args, **_kwargs: {"final_response": "partial", "partial": True},
    )

    result = agent.run_conversation("test")

    assert result["partial"] is True
    assert reasons == ["turn_complete"]


def test_turn_wrapper_closes_idle_request_pool_when_loop_raises(monkeypatch):
    agent = AIAgent.__new__(AIAgent)
    reasons = []
    agent._close_idle_cached_request_openai_client = (
        lambda *, reason: reasons.append(reason)
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("loop failed")

    monkeypatch.setattr("agent.conversation_loop.run_conversation", fail)

    with pytest.raises(RuntimeError, match="loop failed"):
        agent.run_conversation("test")
    assert reasons == ["turn_complete"]


@pytest.mark.parametrize("loop_raises", [False, True])
def test_turn_wrapper_cleanup_failure_never_masks_turn(monkeypatch, loop_raises):
    agent = AIAgent.__new__(AIAgent)

    def cleanup_failure(*, reason):
        raise OSError(f"cleanup failed: {reason}")

    agent._close_idle_cached_request_openai_client = cleanup_failure

    if loop_raises:
        def fail(*_args, **_kwargs):
            raise RuntimeError("original loop failure")

        monkeypatch.setattr("agent.conversation_loop.run_conversation", fail)
        with pytest.raises(RuntimeError, match="original loop failure"):
            agent.run_conversation("test")
    else:
        monkeypatch.setattr(
            "agent.conversation_loop.run_conversation",
            lambda *_args, **_kwargs: {"final_response": "ok"},
        )
        result = agent.run_conversation("test")
        assert result["final_response"] == "ok"
        assert result["cleanup_errors"] == [
            "close_request_client: OSError: cleanup failed: turn_complete"
        ]


def _tcp_state(local_port, remote_port):
    with open("/proc/net/tcp", encoding="ascii") as table:
        next(table)
        for row in table:
            fields = row.split()
            local = int(fields[1].split(":")[1], 16)
            remote = int(fields[2].split(":")[1], 16)
            if local == local_port and remote == remote_port:
                return fields[3]
    return None


@pytest.mark.skipif(
    not Path("/proc/net/tcp").is_file(),
    reason="Linux /proc/net/tcp is required for CLOSE_WAIT proof",
)
def test_turn_wrapper_clears_real_peer_fin_close_wait(monkeypatch):
    """The turn boundary releases a real idle socket after the peer sends FIN."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    server_port = listener.getsockname()[1]
    send_fin = threading.Event()
    stop = threading.Event()
    peer_port = []

    def serve():
        conn, peer = listener.accept()
        peer_port.append(peer[1])
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(4096)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                b"Connection: keep-alive\r\n\r\nOK"
            )
            assert send_fin.wait(timeout=5.0)
            conn.shutdown(socket.SHUT_WR)
            stop.wait(timeout=5.0)
        finally:
            conn.close()
            listener.close()

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    client = httpx.Client(trust_env=False)
    try:
        response = client.get(f"http://127.0.0.1:{server_port}/")
        assert response.text == "OK"
        send_fin.set()

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if peer_port and _tcp_state(peer_port[0], server_port) == "08":
                break
            time.sleep(0.02)
        assert _tcp_state(peer_port[0], server_port) == "08", "peer FIN did not reach CLOSE_WAIT"

        agent = AIAgent.__new__(AIAgent)
        reasons = []

        def close_pool(*, reason):
            reasons.append(reason)
            client.close()

        agent._close_idle_cached_request_openai_client = close_pool
        monkeypatch.setattr(
            "agent.conversation_loop.run_conversation",
            lambda *_args, **_kwargs: {"final_response": "ok"},
        )
        agent.run_conversation("test")

        assert reasons == ["turn_complete"]
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if _tcp_state(peer_port[0], server_port) != "08":
                break
            time.sleep(0.02)
        assert _tcp_state(peer_port[0], server_port) != "08"
    finally:
        client.close()
        stop.set()
        send_fin.set()
        server.join(timeout=5.0)


