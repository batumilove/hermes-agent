from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.harness_eval import HarnessCaseLoader, HarnessCaseValidationError, evaluate_case_files


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_evaluate_case_files_accepts_and_normalizes_valid_jsonl(tmp_path: Path):
    case_file = _write(
        tmp_path / "cases.jsonl",
        [
            {
                "id": "cron-stop-means-pause",
                "failure_type": "action_realization",
                "prompt": "Stop the daily cron",
                "expected_behavior": "pause instead of remove",
                "forbidden_behavior": ["cronjob remove"],
                "required_evidence": "job_id",
                "regression_risk": "destructive ambiguity",
            }
        ],
    )

    report = evaluate_case_files([str(case_file)], dry_run=True)

    assert report["total"] == 1
    assert report["valid"] == 1
    assert report["invalid"] == 0
    case = report["cases"][0]
    assert case["id"] == "cron-stop-means-pause"
    assert case["expected_behavior"] == ["pause instead of remove"]
    assert case["required_evidence"] == ["job_id"]
    assert case["source"].endswith("cases.jsonl:1")


def test_evaluate_case_files_reports_bad_failure_type_and_empty_behaviors(tmp_path: Path):
    case_file = _write(
        tmp_path / "bad.jsonl",
        [
            {
                "id": "bad-type",
                "failure_type": "model_confusion",
                "prompt": "Do it",
            }
        ],
    )

    report = evaluate_case_files([str(case_file)], dry_run=True)

    assert report["total"] == 1
    assert report["valid"] == 0
    assert report["invalid"] == 1
    assert any("failure_type" in error["message"] for error in report["errors"])
    assert any("expected_behavior or forbidden_behavior" in error["message"] for error in report["errors"])


def test_evaluate_case_files_detects_duplicate_ids_across_files(tmp_path: Path):
    row = {
        "id": "duplicate",
        "failure_type": "reasoning",
        "prompt": "x",
        "expected_behavior": ["explain"],
    }
    first = _write(tmp_path / "a.jsonl", [row])
    second = _write(tmp_path / "b.jsonl", [row])

    report = evaluate_case_files([str(first), str(second)], dry_run=True)

    assert report["total"] == 2
    assert report["valid"] == 1
    assert report["invalid"] == 1
    assert any("duplicate id" in error["message"] for error in report["errors"])


def test_loader_rejects_invalid_jsonl_with_line_number(tmp_path: Path):
    path = tmp_path / "broken.jsonl"
    path.write_text('{"id": "ok"}\nnot-json\n', encoding="utf-8")

    report = evaluate_case_files([str(path)], dry_run=True)

    assert report["total"] == 2
    assert report["invalid"] == 2
    assert any(error["source"].endswith("broken.jsonl:2") for error in report["errors"])


def test_loader_expands_globs_and_fails_for_missing_matches(tmp_path: Path):
    _write(
        tmp_path / "one.jsonl",
        [{"id": "one", "failure_type": "reasoning", "prompt": "p", "expected_behavior": ["think"]}],
    )

    loader = HarnessCaseLoader()
    loaded = list(loader.load_paths([str(tmp_path / "*.jsonl")]))
    assert len(loaded) == 1

    with pytest.raises(HarnessCaseValidationError):
        list(loader.load_paths([str(tmp_path / "missing*.jsonl")]))
