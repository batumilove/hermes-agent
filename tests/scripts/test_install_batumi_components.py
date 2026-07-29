from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from scripts.install_batumi_components import (
    Component,
    ComponentError,
    checkout_component,
    load_components,
)

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / ".github" / "batumi-components.lock.yaml"


def test_installer_entrypoint_imports_repo_modules_outside_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "install_batumi_components.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--verify-only" in result.stdout


def test_component_lock_pins_every_external_repository() -> None:
    components = load_components(LOCK)
    assert {component.repository for component in components} == {
        "batumilove/hermes-extensions",
        "batumilove/hermes-tinyfish-plugin",
        "batumilove/hermes-skills",
        "batumilove/hermes-ops",
    }
    assert all(len(component.commit) == 40 for component in components)


def test_operational_workflows_use_the_locked_ops_commit() -> None:
    components = load_components(LOCK)
    ops_sha = next(
        component.commit
        for component in components
        if component.repository == "batumilove/hermes-ops"
    )
    workflows = [
        ROOT / ".github" / "workflows" / "deploy-compose.yml",
        ROOT / ".github" / "workflows" / "promote-compose.yml",
        ROOT / ".github" / "workflows" / "stack-ci-shadow.yml",
        ROOT
        / ".github"
        / "workflows"
        / "staging-telegram-socket-diagnostics.yml",
    ]
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        refs = [
            line.rsplit("@", 1)[1].strip()
            for line in text.splitlines()
            if "uses: batumilove/hermes-ops/" in line
        ]
        assert refs
        assert set(refs) == {ops_sha}


def test_deploy_workflow_allows_pre_cutover_candidate_dispatch() -> None:
    workflow = ROOT / ".github" / "workflows" / "deploy-compose.yml"
    assert "workflow_dispatch:" in workflow.read_text(encoding="utf-8")


def test_component_lock_rejects_moving_refs(tmp_path: Path) -> None:
    payload = yaml.safe_load(LOCK.read_text(encoding="utf-8"))
    payload["components"][0]["commit"] = "main"
    invalid = tmp_path / "lock.yaml"
    invalid.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ComponentError, match="full lowercase SHA"):
        load_components(invalid)


def test_checkout_verifies_exact_detached_commit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.test"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    (source / "README.md").write_text("pinned\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    component = Component(
        "fixture",
        "local/fixture",
        str(source),
        commit,
        "operations",
        (),
    )
    destination = tmp_path / "checkout"
    checkout_component(component, destination)
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=destination,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == commit
    )
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=destination,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
