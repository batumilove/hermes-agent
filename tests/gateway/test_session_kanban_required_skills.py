"""Owner-side Kanban policy cannot run with partially loaded mandatory skills."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("loaded", [[], ["kanban-worker"], ["restart-window-bundling"]])
def test_partial_mandatory_skills_refuse_before_execution(monkeypatch, loaded):
    from gateway.session_kanban import _run_task_turns
    from hermes_cli.kanban_worker_policy import KANBAN_MANDATORY_WORKER_SKILLS
    monkeypatch.setattr("agent.skill_commands.build_preloaded_skills_prompt",
                        lambda *args, **kwargs: ("partial policy", loaded, []))
    agent = SimpleNamespace(session_id="test-owner", run_conversation=Mock())
    context = {"skills": list(KANBAN_MANDATORY_WORKER_SKILLS)}
    with pytest.raises(ValueError, match="kanban_skills_unavailable"):
        _run_task_turns(agent, {"text": "work task"}, [], context)
    agent.run_conversation.assert_not_called()
