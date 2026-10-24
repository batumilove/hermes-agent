"""Authenticated policy fixtures load through the worker's real skill boundary."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from agent.skill_commands import build_preloaded_skills_prompt
from hermes_cli.kanban_worker_policy import KANBAN_MANDATORY_WORKER_SKILLS
from tests.e2e.core._policy_skills import FIXTURE, provision_policy_skills
from tests.e2e.core.kanban._helpers import Board
from tests.fakes.fake_llm_provider import FakeLLMServer, Text, ToolCall
from tools.skills_tool import skill_view


def _required_cli_preload(monkeypatch):
    import cli

    result = build_preloaded_skills_prompt(list(KANBAN_MANDATORY_WORKER_SKILLS))
    thread = threading.Thread(target=lambda: None)
    thread.start()
    obj = SimpleNamespace(_preload_skills_thread=thread, _preload_skills_result=result,
                          system_prompt="base prompt", preloaded_skills=[])
    monkeypatch.setenv("HERMES_REQUIRED_PRELOADED_SKILLS", ",".join(KANBAN_MANDATORY_WORKER_SKILLS))
    cli.HermesCLI.finalize_preloaded_skills(obj)
    return obj


def test_authenticated_policies_reach_the_real_required_preload(monkeypatch, tmp_path):
    home = tmp_path / "home" / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    provision_policy_skills(home)
    source = json.loads((FIXTURE / "SOURCE.json").read_text(encoding="utf-8"))
    cli = _required_cli_preload(monkeypatch)
    assert set(cli.preloaded_skills) == set(KANBAN_MANDATORY_WORKER_SKILLS)
    for name in KANBAN_MANDATORY_WORKER_SKILLS:
        viewed = json.loads(skill_view(name, preprocess=False))
        assert viewed["success"]
        assert viewed["content"] in cli.system_prompt
        path = Path(viewed["skill_dir"]) / "SKILL.md"
        assert path.is_relative_to(home)
        relative = path.relative_to(home / "skills").as_posix()
        assert hashlib.sha256(viewed["content"].encode("utf-8")).hexdigest() == source["files"][relative]["sha256"]
    assert "Coding/review completion does not authorize intake pause" in cli.system_prompt
    assert "This is mandatory, not a preference." in cli.system_prompt


def test_missing_installed_policy_still_blocks_the_real_worker_gate(monkeypatch, tmp_path):
    home = tmp_path / "home" / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    provision_policy_skills(home)
    (home / "skills/software-development/restart-window-bundling/SKILL.md").unlink()
    with pytest.raises(ValueError, match="Required preloaded skill unavailable: restart-window-bundling"):
        _required_cli_preload(monkeypatch)


@pytest.mark.parametrize("fault", ["missing", "archive-tampered", "source-tampered"])
def test_invalid_policy_assets_fail_before_provisioning(tmp_path, fault):
    fixture = tmp_path / "fixture"
    shutil.copytree(FIXTURE, fixture)
    if fault == "missing":
        (fixture / "skills.zip").unlink()
    else:
        path = fixture / ("skills.zip" if fault == "archive-tampered" else "SOURCE.json")
        path.write_bytes(path.read_bytes() + b"\n")
    home = tmp_path / "home" / ".hermes"
    with pytest.raises((OSError, ValueError)):
        provision_policy_skills(home, fixture=fixture)
    assert not home.exists()


def test_authentic_validator_and_templates_remain_executable(tmp_path):
    home = tmp_path / "home" / ".hermes"
    provision_policy_skills(home)
    skill = home / "skills/software-development/restart-window-bundling"
    template = json.loads((skill / "templates/restart-window-bundle.json").read_text(encoding="utf-8"))
    assert template["schema_version"]
    setup = subprocess.run(
        [sys.executable, "-B", "-c",
         "import json,pathlib,runpy,sys; fixture=runpy.run_path(sys.argv[1]); "
         "pathlib.Path(sys.argv[2]).write_text(json.dumps(fixture['sealed_manifest']()),encoding='utf-8')",
         str(skill / "scripts/test_validate_bundle.py"), str(home / "sealed.json")],
        cwd=home, env={**os.environ, "HERMES_HOME": str(home)},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert setup.returncode == 0, setup.stdout + setup.stderr
    validator = skill / "scripts/validate_bundle.py"
    sealed = home / "sealed.json"
    valid = subprocess.run([sys.executable, "-B", str(validator), str(sealed)],
                           cwd=home, capture_output=True, text=True, timeout=30, check=False)
    assert valid.returncode == 0, valid.stdout + valid.stderr
    manifest = json.loads(sealed.read_text(encoding="utf-8"))
    manifest["authorization"]["approved_payload_sha256"] = "0" * 64
    sealed.write_text(json.dumps(manifest), encoding="utf-8")
    invalid = subprocess.run([sys.executable, "-B", str(validator), str(sealed)],
                             cwd=home, capture_output=True, text=True, timeout=30, check=False)
    assert invalid.returncode != 0
    assert "BLOCKED" in invalid.stdout


@pytest.mark.platforms("linux")
@pytest.mark.live_system_guard_bypass
def test_real_dispatched_worker_receives_both_authentic_policy_bodies(tmp_path):
    def complete(record):
        if record["body"]["messages"][-1].get("role") == "tool":
            return Text("isolated proof complete")
        return ToolCall("kanban_complete", {"summary": "isolated policy preload proof"})

    with FakeLLMServer(complete) as provider:
        board = Board(tmp_path, provider.base_url)
        try:
            body = ("Restart impact: restart_free\nTarget processes: disposable test worker\n"
                    "Activation grouping: standalone\nEvidence: only isolated test state\n"
                    "Rollback identity: disposable scratch home")
            task_id = board.create("Authentic policy preload proof", "--body", body)
            board.dispatch()
            pid = board.task(task_id)["worker_pid"]
            assert pid, board.diag(task_id)
            board.wait_worker_exit(task_id, int(pid))
            assert board.task(task_id)["status"] == "done", board.diag(task_id)
            messages = provider.main_requests()[0]["messages"]
            request_text = "\n".join(str(message.get("content", "")) for message in messages)
            for relative in ("devops/kanban-worker", "software-development/restart-window-bundling"):
                content = (board.hermes_home / "skills" / relative / "SKILL.md").read_text(encoding="utf-8")
                assert content in request_text
        finally:
            board.kill_workers()
