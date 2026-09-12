"""Regression: mid-turn context-engine detach must not crash the turn.

Incident (Telegram DM 407304892:219234, 2026-08-29 ~04:10 UTC): a long-running
gateway turn was in flight when the gateway's agent-cache eviction path ran
``AIAgent._shutdown_owned_context_engine()``, which sets
``agent.context_compressor = None``. The conversation loop's pre-API
compression gate then called ``_compressor.should_compress(...)`` on the
detached engine and died with::

    AttributeError: 'NoneType' object has no attribute 'should_compress'

wrapped by the gateway as "Sorry, I encountered an unexpected error."

Contract: the loop reads the engine from the agent at each gate, treats a
missing/detached engine as "compression unavailable" (skip compression,
complete the turn), and never raises from the gate — the same tolerance the
tool-loop tail already grants aux/tool-loop agents that intentionally run
without a context engine.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

# Repo root = three levels up from tests/agent/<file>.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


class _MockHandler(BaseHTTPRequestHandler):
    captured_requests: list = []
    response_queue: list = []

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(length).decode())
        type(self).captured_requests.append(req)
        is_stream = req.get("stream") is True
        if type(self).response_queue:
            resp = type(self).response_queue.pop(0)
        else:
            resp = _text_resp("DONE")
        msg = resp["choices"][0]["message"]
        if is_stream:
            content = msg.get("content") or ""
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            chunks = [{"id": "m", "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}]
            if content:
                chunks.append({"id": "m", "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]})
            chunks.append({"id": "m", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
            for c in chunks:
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            body = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a, **kw):  # silence the default stderr logging
        pass


def _text_resp(text: str) -> dict:
    return {
        "id": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }


def _tc_resp(name: str, args: str = "{}") -> dict:
    return {
        "id": "m",
        "choices": [{"index": 0, "message": {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "call_1", "type": "function",
                            "function": {"name": name, "arguments": args}}]},
            "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }


@pytest.fixture()
def agent_env():
    """Mock provider + isolated HERMES_HOME, yielding (agent, handler)."""
    _MockHandler.captured_requests = []
    _MockHandler.response_queue = []
    srv = HTTPServer(("127.0.0.1", 0), _MockHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()

    test_home = tempfile.mkdtemp(prefix="hermes_e2e_engine_detach_")
    os.makedirs(os.path.join(test_home, ".hermes"))
    prev_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = os.path.join(test_home, ".hermes")

    # HERMES_HOME is set before import so agent_init reads the isolated home.
    # NOTE: do NOT purge agent.*/run_agent from sys.modules here — module
    # re-import changes class identities and breaks cross-file identity
    # checks (e.g. test_context_engine_select_context's bound-method
    # identity comparison against ContextEngine.select_context).
    from run_agent import AIAgent

    agent = AIAgent(
        api_key="test-key", base_url=f"http://127.0.0.1:{port}/v1",
        provider="openai-compat", model="test-model",
        max_iterations=10, enabled_toolsets=[],
        quiet_mode=True, skip_context_files=True, skip_memory=True,
        save_trajectories=False, platform="cli",
    )

    try:
        yield agent, _MockHandler
    finally:
        srv.shutdown()
        shutil.rmtree(test_home, ignore_errors=True)
        if prev_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = prev_home


def test_turn_completes_when_engine_detached_mid_turn(agent_env):
    """Cache eviction detaches the engine after tool execution; the loop gate
    must skip compression and complete the turn instead of raising
    AttributeError: 'NoneType' object has no attribute 'should_compress'."""
    agent, handler = agent_env
    agent.valid_tool_names = {"execute_code"}
    handler.response_queue.append(_tc_resp("execute_code", '{"code": "print(1)"}'))
    handler.response_queue.append(_text_resp("second"))

    # Simulate gateway cache eviction: the engine detaches itself from the
    # agent while the turn is still in flight.  Production eviction nulls
    # agent.context_compressor under _context_engine_shutdown_lock between
    # the first provider response and the second API request.  The
    # pre-API compression gate at conversation_loop.py:2726-2732 is the
    # crash site: it reads _compressor = agent.context_compressor and then
    # calls _compressor.should_compress(request_pressure_tokens).
    #
    # The update_from_response hook fires too late in this mock turn, so we
    # instead wrap note_request_rough_estimate — called via getattr at
    # conversation_loop.py:2646-2650 on every loop iteration, *before* the
    # compressor read at :2686.  Detaching on the second call reproduces the
    # second-request gate crash exactly.
    engine = agent.context_compressor
    original_note = engine.note_request_rough_estimate
    _call_count = {"n": 0}
    detached = {"done": False}

    def _evicting_note(tokens):
        original_note(tokens)
        _call_count["n"] += 1
        if _call_count["n"] == 2 and not detached["done"]:
            detached["done"] = True
            agent.context_compressor = None

    engine.note_request_rough_estimate = _evicting_note

    result = agent.run_conversation(
        "hello",
        conversation_history=[{"role": "assistant", "content": "previous"}],
        task_id="t",
    )

    assert result["messages"][-1]["content"] == "second"
    assert not result.get("partial", False)
    assert agent.context_compressor is None
