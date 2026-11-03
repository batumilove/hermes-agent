"""Exercise cache write authorization for reusable CI callers."""
from pathlib import Path
import re

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def evaluate(expression, event, ref, default, present=True):
    """Evaluate the small boolean subset used by the cache guards."""
    values = {
        "github.event_name": event,
        "github.ref": ref,
        "github.event.repository.default_branch": default,
    }
    expression = re.sub(
        r"format\('refs/heads/\{0\}', github.event.repository.default_branch\)",
        repr("refs/heads/" + default), expression,
    )
    expression = re.sub(r"hashFiles\('[^']+'\)", repr("digest" if present else ""), expression)
    for key in sorted(values, key=len, reverse=True):
        expression = expression.replace(key, repr(values[key]))
    return eval(expression.replace("&&", " and ").replace("||", " or "), {"__builtins__": {}}, {})


def guards():
    for name in ("tests.yml", "ci.yaml"):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if "cache/save@" in step.get("uses", "") or step.get("name", "").startswith("Save baseline cache"):
                    yield name, step


@pytest.mark.parametrize("default", ["main", "batumi/live", "release/stable"])
def test_only_default_branch_push_can_write(default):
    selected = list(guards())
    assert len(selected) == 3
    for name, step in selected:
        for event in ("push", "pull_request", "pull_request_target", "workflow_dispatch", "schedule", "workflow_call"):
            for ref in ("refs/heads/" + default, "refs/heads/feature", "refs/pull/42/merge", "refs/tags/v1"):
                expected = event == "push" and ref == "refs/heads/" + default
                assert bool(evaluate(step["if"], event, ref, default)) == expected, (name, step["name"], event, ref)
        if "cache/save@" in step.get("uses", ""):
            assert not evaluate(step["if"], "push", "refs/heads/" + default, default, present=False)


def test_restore_and_save_use_same_default_branch_namespace():
    for name in ("tests.yml", "ci.yaml"):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        steps = [s for job in workflow["jobs"].values() for s in job.get("steps", [])]
        restores = [s for s in steps if "cache/restore@" in s.get("uses", "")]
        saves = [s for s in steps if "cache/save@" in s.get("uses", "")]
        assert len(restores) == len(saves) == 1
        prefix = restores[0]["with"]["restore-keys"].strip()
        assert "${{ github.event.repository.default_branch }}" in prefix
        assert prefix.startswith("trusted-")
        assert saves[0]["with"]["key"].startswith(prefix)
        assert restores[0]["with"]["key"] == prefix + "never-exact"
