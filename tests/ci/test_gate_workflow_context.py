"""Replay the aggregate gate with large reports and every dependency result."""

import json
from pathlib import Path

import pytest
import yaml

from tests.ci import workflow_steps

ROOT = Path(__file__).resolve().parents[2]
JOB = yaml.safe_load((ROOT / ".github/workflows/ci.yaml").read_text())["jobs"]["all-checks-pass"]
STEP = next(step for step in JOB["steps"] if step.get("id") == "evaluate")


def _context():
    return {"inputs": {"release": False}, "needs": {
        name: {"result": "success", "outputs": {}} for name in JOB["needs"]
    }}


@pytest.mark.platforms("posix")
def test_large_review_reports_do_not_prevent_gate_starting():
    context = _context()
    context["needs"]["lockfile-diff"]["outputs"]["review_status"] = "x" * 1048576
    output = workflow_steps.outputs(STEP, context, ROOT)
    assert json.loads(output["needs-json"]) == dict.fromkeys(JOB["needs"], "success")


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("job", JOB["needs"])
@pytest.mark.parametrize("result", ["failure", "cancelled"])
def test_compact_context_still_blocks_every_unsuccessful_dependency(job, result):
    context = _context()
    context["needs"][job]["result"] = result
    with pytest.raises(AssertionError, match=f"job\\(s\\) failed: {job}"):
        workflow_steps.outputs(STEP, context, ROOT)


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("release,lane", [(False, "python"), (True, None)])
def test_compact_context_preserves_required_skip_policy(release, lane):
    context = _context()
    context["inputs"]["release"] = release
    if lane:
        context["needs"]["detect"]["outputs"][lane] = "true"
    context["needs"]["tests"]["result"] = "skipped"
    with pytest.raises(AssertionError, match="job\\(s\\) failed: tests"):
        workflow_steps.outputs(STEP, context, ROOT)
