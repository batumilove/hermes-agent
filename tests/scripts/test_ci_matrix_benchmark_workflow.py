from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def load_workflow() -> dict:
    # BaseLoader preserves the GitHub Actions `on` key and expression strings.
    return yaml.load(CI_WORKFLOW.read_text(), Loader=yaml.BaseLoader)


def test_ci_dispatch_exposes_only_supported_python_slice_counts():
    workflow = load_workflow()

    dispatch = workflow["on"]["workflow_dispatch"]
    selector = dispatch["inputs"]["python_slice_count"]

    assert selector["type"] == "choice"
    assert selector["required"] == "true"
    assert selector["default"] == "8"
    assert selector["options"] == ["8", "6", "5"]


def test_required_ci_defaults_to_eight_slices_outside_benchmarks():
    workflow = load_workflow()

    assert "pull_request" in workflow["on"]
    assert workflow["on"]["push"]["branches"] == ["main", "batumi/live"]
    assert workflow["jobs"]["tests"]["uses"] == "./.github/workflows/tests.yml"
    assert workflow["jobs"]["tests"]["with"]["slice_count"] == (
        "${{ fromJSON(inputs.python_slice_count || '8') }}"
    )
