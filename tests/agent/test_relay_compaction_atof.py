"""Compaction marks through the real NeMo Relay ATOF plugin, and the freshness reset they drive.

Relay records an LLM start projected to the current user turn while its agent scope is not fresh. A
``compaction`` mark on the session's own stack makes the agent fresh again, so the next LLM start
records the full compacted history. A failed attempt is only a ``compaction.attempt`` mark and must
not reset anything.
"""

from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from tests.agent.test_relay_llm import relay_turn as _relay_turn

relay_turn = _relay_turn

SESSION_ID = "compaction-atof-session"
SECRET = "TOPSECRET_TRANSCRIPT_TEXT"
LLM_NAME = "openai.chat_completions"
ABORT = object()  # summary sentinel: fail the summary with abort_on_summary_failure set


class _TodoStore:
    def format_for_injection(self):
        return ""


class _Agent:
    def __init__(self, compressor):
        self.context_compressor = compressor
        self.session_id = SESSION_ID
        self.platform = "cli"
        self.model = "test/main-model"
        self.provider = "test-provider"
        self.tools = []
        self._compression_feasibility_checked = True
        self.compression_in_place = True
        self._memory_manager = None
        self._session_db = None
        self._todo_store = _TodoStore()
        self._cached_system_prompt = None

    def _emit_status(self, _message):
        pass

    def _emit_warning(self, _message):
        pass

    def _invalidate_system_prompt(self):
        self._cached_system_prompt = None

    def _build_system_prompt(self, system_message):
        return system_message

    def commit_memory_session(self, _messages):
        pass


def _transcript():
    filler = " ".join(["context"] * 200)
    msgs = [{"role": "system", "content": "system prompt"}]
    for idx in range(10):
        msgs.append({"role": "user", "content": f"user message {idx} {SECRET} {filler}"})
        msgs.append({"role": "assistant", "content": f"assistant reply {idx} {SECRET} {filler}"})
    return msgs


def _chat_response(_request):
    return {
        "id": "r", "object": "chat.completion", "created": 0, "model": "test/main-model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
    }


def _llm(messages, request_id, *, role="primary", defer=False):
    from agent import relay_llm

    relay_llm.execute(
        {"model": "test/main-model", "messages": messages}, _chat_response, name=LLM_NAME,
        model_name="test/main-model", session_id=SESSION_ID,
        metadata={"api_mode": "chat_completions", "api_request_id": request_id, "call_role": role},
        defer_logical_completion=defer,
    )


def _write_plugins_toml(tmp_path, atof_dir):
    config = tmp_path / "plugins.toml"
    config.write_text(
        f"""version = 1

[[components]]
kind = "observability"
enabled = true

[components.config]
version = 4

[components.config.atof]
enabled = true

[[components.config.atof.sinks]]
type = "file"
output_directory = {json.dumps(str(atof_dir))}
filename = "events.jsonl"
mode = "overwrite"
""",
        encoding="utf-8",
    )
    return config


def _run_turn_with_compaction(tmp_path, monkeypatch, summary, *, exercise_overlap=None, main_role="primary"):
    """One Relay turn: two LLM calls, one compression attempt, one more LLM call. Returns ATOF events
    and the message count of the last request."""
    relay = pytest.importorskip("nemo_relay")
    if getattr(relay, "_native", None) is None:
        pytest.skip("NeMo Relay native binding is unavailable on this platform")

    from agent import relay_runtime
    from agent.context_compressor import ContextCompressor
    from agent.conversation_compression import compress_context

    hermes_home, atof_dir = tmp_path / "hermes-home", tmp_path / "atof"
    hermes_home.mkdir()
    atof_dir.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv(relay_runtime.RELAY_PLUGINS_CONFIG_ENV, str(_write_plugins_toml(tmp_path, atof_dir)))
    relay_runtime._reset_for_tests()

    with patch("agent.context_compressor.get_model_context_length", return_value=100_000):
        compressor = ContextCompressor(
            model="test/main-model", provider="test-provider", threshold_percent=0.50, quiet_mode=True,
            config_context_length=100_000, abort_on_summary_failure=summary is ABORT,
        )
    compressor.tail_token_budget = 10
    agent = _Agent(compressor)
    transcript = _transcript()
    coordinator = relay_runtime.SESSION_COORDINATOR
    profile_key = relay_runtime.current_profile_key()
    try:
        lease = coordinator.acquire_conversation(profile_key=profile_key, session_id=SESSION_ID, platform="cli")
        turn = coordinator.begin_turn(lease, turn_id="turn-1", task_id="task-1")
        try:
            if exercise_overlap is not None:
                exercise_overlap(coordinator, turn)
            _llm(transcript[:-2], "r1", role=main_role)
            _llm(transcript, "r2", role=main_role)
            with patch.object(compressor, "_generate_summary", return_value=None if summary is ABORT else summary):
                compressed, _ = compress_context(agent, transcript, "system prompt", approx_tokens=80_000)
            last_request = compressed + [{"role": "user", "content": "next question"}]
            _llm(last_request, "r3", role=main_role)
        finally:
            coordinator.end_turn(turn, outcome="success")
            coordinator.finalize_conversation(profile_key=profile_key, session_id=SESSION_ID)
    finally:
        relay_runtime._reset_for_tests()

    events = [
        json.loads(line)
        for line in (atof_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return events, len(last_request)



def _llm_start_message_counts(events):
    counts = []
    for event in events:
        if event.get("name") == LLM_NAME and event.get("scope_category") == "start":
            data = event.get("data") or {}
            counts.append(len((data.get("content") or data).get("messages") or []))
    return counts


def _compaction_marks(events):
    return [e for e in events if e.get("kind") == "mark" and str(e.get("name", "")).startswith("compaction")]


@pytest.mark.parametrize(
    ("summary", "method"), [("SANITIZED SUMMARY", "llm_summary"), (None, "deterministic_fallback")],
)
@pytest.mark.parametrize("main_role", ["primary", "delegated", "fallback"])
def test_committed_compaction_mark_resets_freshness_for_the_next_llm_start(tmp_path, monkeypatch, summary, method, main_role):
    events, last_request_len = _run_turn_with_compaction(tmp_path, monkeypatch, summary, main_role=main_role)

    [mark] = _compaction_marks(events)
    assert mark["name"] == "compaction"
    assert mark["data_schema"] == {"name": "hermes.compaction", "version": "1"}
    assert (mark["data"]["kind"], mark["data"]["outcome"], mark["data"]["method"]) == ("summarize", "committed", method)
    [turn_start] = [e for e in events if e.get("name") == "hermes.turn" and e.get("scope_category") == "start"]
    assert mark["parent_uuid"] == turn_start["uuid"]
    raw = json.dumps(mark)
    assert SECRET not in raw and "SANITIZED SUMMARY" not in raw

    _first, second, after = _llm_start_message_counts(events)
    assert second < len(_transcript())  # not fresh: projected to the current user turn
    assert after == last_request_len  # fresh again after the compaction mark: full history


@pytest.mark.parametrize("main_role", ["primary", "delegated", "fallback"])
def test_failed_attempt_is_an_attempt_mark_and_leaves_freshness_alone(tmp_path, monkeypatch, main_role):
    events, last_request_len = _run_turn_with_compaction(tmp_path, monkeypatch, ABORT, main_role=main_role)

    [mark] = _compaction_marks(events)
    assert mark["name"] == "compaction.attempt"
    assert (mark["data"]["outcome"], mark["data"]["failure_class"]) == ("failed", "summary_generation_aborted")
    *_, after = _llm_start_message_counts(events)
    assert after < last_request_len


def test_native_freshness_isolated_between_overlapping_calls_roles_and_turns(tmp_path, monkeypatch):
    from agent import relay_llm

    def overlap(coordinator, turn):
        transcript = _transcript()
        _llm(transcript, "overlap-a", defer=True)
        _llm(transcript, "overlap-b", defer=True)
        _llm(transcript, "auxiliary", role="auxiliary:summary")
        relay_llm.complete_logical_call("overlap-a", outcome="success")
        # A completed stack can be reused while B still owns its independent stack.
        _llm(transcript, "reuse-a")
        relay_llm.complete_logical_call("overlap-b", outcome="success")
        second = coordinator.begin_turn(turn.lease, turn_id="turn-2", task_id="task-2")
        try:
            _llm(transcript, "other-turn")
        finally:
            coordinator.end_turn(second, outcome="success")

    events, last_len = _run_turn_with_compaction(tmp_path, monkeypatch, "SUMMARY", exercise_overlap=overlap)
    first, parallel, auxiliary, reused, other_turn, resumed, before_mark, after_mark = _llm_start_message_counts(events)
    full = len(_transcript())
    assert first == parallel == auxiliary == other_turn == full
    assert reused < full and resumed < full - 2 and before_mark < full
    assert after_mark == last_len
    starts = [e for e in events if e.get("name") == LLM_NAME and e.get("scope_category") == "start"]
    assert starts[0]["parent_uuid"] != starts[1]["parent_uuid"]
    assert starts[0]["parent_uuid"] != starts[3]["parent_uuid"]


def test_compaction_refresh_survives_an_unmarked_overlapping_call_finishing_first(tmp_path, monkeypatch):
    from agent import relay_compaction, relay_llm

    def overlap(coordinator, turn):
        transcript = _transcript()
        _llm(transcript, "marked-a", defer=True)
        _llm(transcript, "unmarked-b", defer=True)
        assert relay_compaction.emit_compaction_mark(SESSION_ID, "compaction", {"outcome": "committed"})
        # B must not replace the refreshed A stack merely because it finishes first.
        relay_llm.complete_logical_call("unmarked-b", outcome="success")
        relay_llm.complete_logical_call("marked-a", outcome="success")
        _llm(transcript, "after-overlapping-rewrite")
        _llm(transcript, "after-consumed-refresh")

    events, last_len = _run_turn_with_compaction(tmp_path, monkeypatch, "SUMMARY", exercise_overlap=overlap)
    first, second, refreshed, stale, resumed, before_mark, after_mark = _llm_start_message_counts(events)
    full = len(_transcript())
    assert first == second == refreshed == full
    assert stale < full and resumed < full - 2 and before_mark < full
    assert after_mark == last_len
    assert len(_compaction_marks(events)) == 2


def test_native_mark_worker_finishes_after_timeout_and_turn_close_without_touching_sibling(relay_turn, monkeypatch):
    from agent import relay_compaction, relay_llm, relay_runtime
    from hermes_constants import get_hermes_home, get_hermes_home_override, reset_hermes_home_override, set_hermes_home_override

    relay, turn = relay_turn
    coordinator, lease = relay_runtime.SESSION_COORDINATOR, turn.lease
    relay_llm.execute(
        {"model": "test/main-model", "messages": _transcript()}, _chat_response,
        name=LLM_NAME, model_name="test/main-model", session_id=lease.session_id,
        metadata={"api_mode": "chat_completions", "api_request_id": "before-timeout"},
    )
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    errors, profiles, parents = [], [], []
    original = relay.scope.event

    def delayed_event(name, **kwargs):
        profiles.append(get_hermes_home_override())
        started.set()
        release.wait()
        try:
            profiles.append(get_hermes_home_override())
            original(name, **kwargs)
            parents.append(kwargs["handle"])
        except Exception as exc:
            errors.append(exc)
            raise
        finally:
            finished.set()

    monkeypatch.setattr(relay.scope, "event", delayed_event)
    monkeypatch.setattr(relay_compaction, "_stalled", False)
    home = str(get_hermes_home())
    token = set_hermes_home_override(home)
    sibling = None
    try:
        with monkeypatch.context() as timeout_patch:
            timeout_patch.setattr(relay_runtime, "SCOPE_OP_TIMEOUT", 2.0)
            with pytest.raises(TimeoutError, match="Relay scope operation exceeded"):
                relay_compaction.emit_compaction_mark(lease.session_id, "compaction", {"outcome": "committed"})
        assert started.wait(2)
        sibling = coordinator.begin_turn(lease, turn_id="live-sibling", task_id="task-sibling")
        coordinator.end_turn(turn, outcome="success")
        assert not turn.idle_logical_llm_contexts
        release.set()
        assert finished.wait(2)
        assert errors == []
        assert profiles == [home, home]
        assert parents == [turn.handle]
        top = lease.host.run_in_session(lease.session, relay_runtime._current_top, relay, scope_context=sibling.context)
        assert relay_runtime._same_handle(top, sibling.handle)
        assert relay_runtime.active_turn() is sibling
    finally:
        release.set()
        assert finished.wait(2)
        if sibling is not None:
            coordinator.end_turn(sibling, outcome="success")
        reset_hermes_home_override(token)
