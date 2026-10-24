from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

class _DummyCLI:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.session_id = "session-123"
        self.system_prompt = "base prompt"
        self.preloaded_skills = []

    def show_banner(self):
        return None

    def show_tools(self):
        return None

    def show_toolsets(self):
        return None

    def run(self):
        return None

def _real_finalize(cli_obj):
    """Call the real HermesCLI.finalize_preloaded_skills on a dummy object."""
    return _REAL_FINALIZE(cli_obj)

def _capture_real_finalize():
    import cli as cli_mod
    return cli_mod.HermesCLI.__dict__["finalize_preloaded_skills"]

_REAL_FINALIZE = _capture_real_finalize()

def test_main_applies_preloaded_skills_to_system_prompt(monkeypatch):
    import cli as cli_mod

    created = {}

    def fake_cli(**kwargs):
        created["cli"] = _DummyCLI(**kwargs)
        return created["cli"]

    monkeypatch.setattr(cli_mod, "HermesCLI", fake_cli)
    monkeypatch.setattr(
        cli_mod,
        "build_preloaded_skills_prompt",
        lambda skills, task_id=None, excluded_loaded_names=None: ("skill prompt", ["hermes-agent-dev", "github-auth"], []),
    )

    with pytest.raises(SystemExit):
        cli_mod.main(skills="hermes-agent-dev,github-auth", list_tools=True)

    cli_obj = created["cli"]
    # The preload now runs in a background thread and is folded in at agent
    # init via finalize_preloaded_skills() (startup-latency change). Drive
    # the finalize explicitly — the same call _init_agent makes.
    _real_finalize(cli_obj)
    assert cli_obj.system_prompt == "base prompt\n\nskill prompt"
    assert cli_obj.preloaded_skills == ["hermes-agent-dev", "github-auth"]

def test_main_raises_for_unknown_preloaded_skill(monkeypatch):
    import cli as cli_mod

    created = {}

    def fake_cli(**kwargs):
        created["cli"] = _DummyCLI(**kwargs)
        return created["cli"]

    monkeypatch.setattr(cli_mod, "HermesCLI", fake_cli)
    monkeypatch.setattr(
        cli_mod,
        "build_preloaded_skills_prompt",
        lambda skills, task_id=None, excluded_loaded_names=None: ("", [], ["missing-skill"]),
    )

    with pytest.raises(SystemExit):
        cli_mod.main(skills="missing-skill", list_tools=True)

    # The all-skills-unknown hard failure now surfaces when the preload is
    # finalized (agent init), preserving the fail-loud contract.
    with pytest.raises(ValueError, match=r"Unknown skill\(s\): missing-skill"):
        _real_finalize(created["cli"])


def test_required_preloaded_skill_fails_closed_on_partial_inventory(monkeypatch):
    """A Kanban worker must not continue when one policy skill is unavailable."""
    import cli as cli_mod

    created = {}

    def fake_cli(**kwargs):
        created["cli"] = _DummyCLI(**kwargs)
        return created["cli"]

    monkeypatch.setenv(
        "HERMES_REQUIRED_PRELOADED_SKILLS",
        "kanban-worker,restart-window-bundling",
    )
    monkeypatch.setattr(cli_mod, "HermesCLI", fake_cli)
    monkeypatch.setattr(
        cli_mod,
        "build_preloaded_skills_prompt",
        lambda skills, task_id=None, **kwargs: (
            "kanban prompt",
            ["kanban-worker"],
            ["restart-window-bundling"],
        ),
    )

    with pytest.raises(SystemExit):
        cli_mod.main(
            skills="kanban-worker,restart-window-bundling",
            list_tools=True,
        )

    with pytest.raises(
        ValueError,
        match="Required preloaded skill unavailable: restart-window-bundling",
    ):
        _real_finalize(created["cli"])
    assert "HERMES_REQUIRED_PRELOADED_SKILLS" not in os.environ

def test_required_preloaded_skill_marker_is_consumed_on_success(monkeypatch):
    """The one-shot worker marker must not leak to nested Hermes commands."""
    import cli as cli_mod

    created = {}

    def fake_cli(**kwargs):
        created["cli"] = _DummyCLI(**kwargs)
        return created["cli"]

    monkeypatch.setenv(
        "HERMES_REQUIRED_PRELOADED_SKILLS",
        "kanban-worker,restart-window-bundling",
    )
    monkeypatch.setattr(cli_mod, "HermesCLI", fake_cli)
    monkeypatch.setattr(
        cli_mod,
        "build_preloaded_skills_prompt",
        lambda skills, task_id=None, **kwargs: (
            "policy prompt",
            ["kanban-worker", "restart-window-bundling"],
            [],
        ),
    )

    with pytest.raises(SystemExit):
        cli_mod.main(
            skills="kanban-worker,restart-window-bundling",
            list_tools=True,
        )
    _real_finalize(created["cli"])

    assert "HERMES_REQUIRED_PRELOADED_SKILLS" not in os.environ
    assert created["cli"].preloaded_skills == [
        "kanban-worker",
        "restart-window-bundling",
    ]

@pytest.mark.parametrize("with_thread", [False, True])
def test_required_preloaded_skills_fail_closed_without_result(monkeypatch, with_thread):
    """Required policy preload cannot silently pass without a completed result."""
    monkeypatch.setenv(
        "HERMES_REQUIRED_PRELOADED_SKILLS",
        "kanban-worker,restart-window-bundling",
    )
    cli_obj = _DummyCLI()
    if with_thread:
        cli_obj._preload_skills_thread = MagicMock()

    with pytest.raises(
        ValueError,
        match="Required preloaded skills unavailable: "
        "kanban-worker, restart-window-bundling",
    ):
        _real_finalize(cli_obj)
