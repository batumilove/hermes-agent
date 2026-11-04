"""Real turn facade brackets hook evidence on success, cancellation and exceptions."""
import pytest

from hermes_cli.plugins import PluginContext, PluginManifest, get_plugin_manager
from tests.agent.test_cross_process_turn_lease import _DB, _agent_with_db
from run_agent import AIAgent


@pytest.mark.parametrize("outcome", ["success", "failed", "interrupted", "exception"])
def test_turn_owner_closes_evidence_after_loop(monkeypatch, outcome):
    manager = get_plugin_manager()
    context = PluginContext(PluginManifest(name="evidence"), manager)
    retained = []
    def observe(**kwargs):
        retained.append(context.retain_turn_evidence(lambda: {"fresh": True}))
    context.register_hook("post_llm_call", observe)
    def run(agent, *_args, **_kwargs):
        manager.invoke_hook("post_llm_call", turn_id=agent._relay_pending_turn_id)
        assert context.collect_turn_evidence(retained[0]).status == "pending"
        if outcome == "exception":
            raise ValueError("offline failure")
        return {"final_response": "ok", "failed": outcome == "failed",
                "interrupted": outcome == "interrupted"}
    monkeypatch.setattr("agent.conversation_loop.run_conversation", run)
    agent = _agent_with_db(_DB())
    if outcome == "exception":
        with pytest.raises(ValueError, match="offline failure"):
            AIAgent.run_conversation(agent, "offline")
    else:
        AIAgent.run_conversation(agent, "offline")
    result = context.collect_turn_evidence(retained[0])
    assert result.status == ("collected" if outcome == "success" else "unverified")
