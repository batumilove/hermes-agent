from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.harness_eval import (
    HarnessCaseLoader,
    HarnessCaseValidationError,
    HarnessTraceLoader,
    HarnessTraceScorer,
    HarnessTraceValidationError,
    TraceRecord,
    evaluate_case_files,
)


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


def test_trace_loader_and_trace_record_from_raw(tmp_path: Path):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(
            {
                "case_id": "abc",
                "tool_calls": [{"name": "cronjob", "args": {"action": "pause"}}],
                "final_response": "Paused.",
            }
        )
        + "\n"
        + "not-json\n",
        encoding="utf-8",
    )

    loader = HarnessTraceLoader()
    items = list(loader.load_paths([str(path)]))
    assert len(items) == 2

    record, errors = TraceRecord.from_raw(items[0][2], source=str(items[0][0]))
    assert record is not None
    assert record.case_id == "abc"
    assert record.tool_calls[0]["name"] == "cronjob"
    assert errors == []

    with pytest.raises(HarnessTraceValidationError):
        list(loader.load_paths([str(tmp_path / "missing*.jsonl")]))


def test_trace_loader_rejects_glob_with_no_matches(tmp_path: Path):
    with pytest.raises(HarnessTraceValidationError):
        list(HarnessTraceLoader().load_paths([str(tmp_path / "missing*.jsonl")]))


def test_trace_scorer_passes_when_required_tool_and_evidence_are_present():
    report = HarnessTraceScorer(
        [
            {
                "id": "cron-stop-means-pause",
                "failure_type": "action_realization",
                "prompt": "Stop cron",
                "expected_behavior": ["pause/disable matching job"],
                "forbidden_behavior": ["cronjob remove"],
                "required_evidence": ["job_id"],
            }
        ]
    ).score_traces(
        [
            {
                "case_id": "cron-stop-means-pause",
                "tool_calls": [
                    {"name": "cronjob", "args": {"action": "pause", "job_id": "abc"}, "result": {"job_id": "abc", "status": "paused"}}
                ],
                "final_response": "Paused cron job abc.",
            }
        ]
    )

    assert report["scored"] == 1
    assert report["passed"] == 1
    result = report["results"][0]
    assert result["status"] == "pass"
    assert "job_id" in result["evidence"]


def test_trace_scorer_fails_for_forbidden_tool_action():
    report = HarnessTraceScorer(
        [
            {
                "id": "cron-stop-means-pause",
                "failure_type": "action_realization",
                "prompt": "Stop cron",
                "expected_behavior": ["pause/disable matching job"],
                "forbidden_behavior": ["cronjob remove"],
            }
        ]
    ).score_traces(
        [
            {
                "case_id": "cron-stop-means-pause",
                "tool_calls": [{"name": "cronjob", "args": {"action": "remove", "job_id": "abc"}, "result": {"status": "removed"}}],
                "final_response": "Removed cron job abc.",
            }
        ]
    )

    result = report["results"][0]
    assert result["status"] == "fail"
    assert any("forbidden" in failure for failure in result["failures"])


def test_trace_scorer_fails_when_required_evidence_is_missing():
    report = HarnessTraceScorer(
        [
            {
                "id": "v1-issue-creation-requires-url",
                "failure_type": "action_realization",
                "prompt": "Create issue",
                "expected_behavior": ["create issue via issue tool"],
                "required_evidence": ["issue URL or number"],
            }
        ]
    ).score_traces(
        [
            {
                "case_id": "v1-issue-creation-requires-url",
                "tool_calls": [{"name": "github", "args": {"action": "create_issue"}, "result": {"status": "created"}}],
                "final_response": "Created the GitHub issue.",
            }
        ]
    )

    result = report["results"][0]
    assert result["status"] == "fail"
    assert any("missing required evidence" in failure for failure in result["failures"])


def test_trace_scorer_fails_unsupported_side_effect_claim():
    report = HarnessTraceScorer(
        [
            {
                "id": "side-effect-claims-require-evidence",
                "failure_type": "action_realization",
                "prompt": "Send update",
                "expected_behavior": ["include message id/url/status handle"],
                "forbidden_behavior": ["claim sent/created without evidence handle"],
            }
        ]
    ).score_traces(
        [
            {
                "case_id": "side-effect-claims-require-evidence",
                "tool_calls": [],
                "final_response": "I sent the message successfully.",
            }
        ]
    )

    result = report["results"][0]
    assert result["status"] == "fail"
    assert any("unsupported side-effect claim" in failure for failure in result["failures"])


def test_trace_scorer_warns_for_uncheckable_expected_behavior():
    report = HarnessTraceScorer(
        [
            {
                "id": "reasoning-case",
                "failure_type": "reasoning",
                "prompt": "Explain",
                "expected_behavior": ["explain trade-offs carefully"],
            }
        ]
    ).score_traces(
        [
            {
                "case_id": "reasoning-case",
                "tool_calls": [],
                "final_response": "Here is an answer.",
            }
        ]
    )

    result = report["results"][0]
    assert result["status"] == "warn"
    assert any("expected behavior not machine-checkable" in warning for warning in result["warnings"])


def test_trace_scorer_fails_for_unknown_case_id():
    report = HarnessTraceScorer(
        [{"id": "known", "failure_type": "reasoning", "prompt": "p", "expected_behavior": ["x"]}]
    ).score_traces([{"case_id": "unknown", "tool_calls": [], "final_response": "nope"}])

    assert report["failed"] == 1
    result = report["results"][0]
    assert result["status"] == "fail"
    assert "no case found" in result["failures"][0]


def test_evaluate_case_files_includes_trace_scoring_when_trace_given(tmp_path: Path):
    case_file = _write(
        tmp_path / "cases.jsonl",
        [
            {
                "id": "cron-stop-means-pause",
                "failure_type": "action_realization",
                "prompt": "Stop cron",
                "expected_behavior": ["pause/disable matching job"],
                "forbidden_behavior": ["cronjob remove"],
                "required_evidence": ["job_id"],
            }
        ],
    )
    trace_file = _write(
        tmp_path / "trace.jsonl",
        [
            {
                "case_id": "cron-stop-means-pause",
                "tool_calls": [
                    {"name": "cronjob", "args": {"action": "pause", "job_id": "abc"}, "result": {"job_id": "abc"}}
                ],
                "final_response": "Paused cron job abc.",
            }
        ],
    )

    report = evaluate_case_files([str(case_file)], dry_run=True, trace=[str(trace_file)])

    assert report["trace_scoring"]["scored"] == 1
    assert report["trace_scoring"]["passed"] == 1


def test_trace_scorer_uses_fixtures_from_filesystem():
    fixture_dir = Path(__file__).resolve().parent.parent / "fixtures" / "harness_eval"
    cases_file = fixture_dir / "cases.jsonl"
    trace_file = fixture_dir / "pass_trace.jsonl"

    report = evaluate_case_files([str(cases_file)], trace=[str(trace_file)])

    assert report["trace_scoring"]["scored"] == 1
    assert report["trace_scoring"]["passed"] == 1
    assert report["trace_scoring"]["results"][0]["case_id"] == "cron-stop-means-pause"
