from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "timings_report.py"
SPEC = importlib.util.spec_from_file_location("ci_timings_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
TIMINGS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TIMINGS)


def _job(name: str, started: str, completed: str) -> dict:
    return {
        "name": name,
        "status": "completed",
        "conclusion": "success",
        "started_at": started,
        "completed_at": completed,
        "duration_s": TIMINGS.dur_s(started, completed),
        "steps": [],
    }


def test_collect_timings_records_actual_head_branch(monkeypatch):
    def fake_api_get(path, token, params=None, list_key=None):
        if path.endswith("/actions/runs/123"):
            return {
                "created_at": "2026-07-22T00:00:00Z",
                "head_branch": "batumi/live",
            }
        if path.endswith("/actions/runs/123/jobs"):
            return {"jobs": []} if list_key is None else []
        if path.endswith("/actions/runs"):
            return []
        raise AssertionError(path)

    monkeypatch.setattr(TIMINGS, "api_get", fake_api_get)

    report = TIMINGS.collect_timings("token", "batumilove/hermes-agent", "123", "a" * 40)

    assert report["head_branch"] == "batumi/live"


def test_html_labels_baseline_with_its_actual_branch():
    current = {
        "run_id": "2",
        "head_sha": "b" * 40,
        "head_branch": "feature/timing",
        "created_at": "2026-07-22T00:00:00Z",
        "jobs": [_job("tests", "2026-07-22T00:00:00Z", "2026-07-22T00:01:00Z")],
    }
    baseline = {
        "run_id": "1",
        "head_sha": "a" * 40,
        "head_branch": "batumi/live",
        "created_at": "2026-07-21T00:00:00Z",
        "jobs": [_job("tests", "2026-07-21T00:00:00Z", "2026-07-21T00:01:00Z")],
    }

    html = TIMINGS.generate_html(current, baseline)

    assert "Baseline: <code>aaaaaaa</code> (batumi/live)" in html
    assert "Baseline: <code>aaaaaaa</code> (main)" not in html


def test_workflow_scopes_baseline_cache_to_pr_target_and_live_push_branch():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "ci-timings-baseline-${{ github.base_ref }}-" in workflow
    assert "ci-timings-baseline-${{ github.ref_name }}-${{ github.run_id }}" in workflow
    assert "github.ref == 'refs/heads/batumi/live'" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
