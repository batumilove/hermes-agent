import json
from pathlib import Path

import pytest

from agent import context_route_outcome_report as report


def write_summary(path: Path, *, cases: list[dict], event_count: int | None = None, mismatches: int | None = None) -> None:
    payload = {
        "schema_version": 1,
        "created_at": "2026-06-04T00:00:00+00:00",
        "profile": "tdai-canary",
        "case_count": len(cases),
        "event_count": event_count if event_count is not None else sum(case.get("event_count", 0) for case in cases),
        "mismatch_event_count": mismatches if mismatches is not None else sum(case.get("advisor_mismatches", 0) for case in cases),
        "review_case_count": sum(1 for case in cases if case.get("needs_review")),
        "cases": cases,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_outcome_report_aggregates_runs_cases_events_and_reviews(tmp_path):
    first = tmp_path / "run-a.json"
    second = tmp_path / "run-b.json"
    write_summary(
        first,
        cases=[
            {
                "case": "web-docs",
                "expected_family": "web",
                "session_id": "s1",
                "returncode": 0,
                "event_count": 2,
                "advisor_mismatches": 0,
                "errors": 0,
                "expected_family_events": 2,
                "unexpected_families": [],
                "route_family_ok": True,
                "needs_review": False,
                "route_families": {"web": 2},
                "routes": {"web_search": 2},
            },
            {
                "case": "file-inspect",
                "expected_family": "file",
                "session_id": "s2",
                "returncode": 0,
                "event_count": 2,
                "advisor_mismatches": 1,
                "errors": 1,
                "expected_family_events": 1,
                "unexpected_families": ["web"],
                "route_family_ok": False,
                "acceptable_families": ["file", "web"],
                "route_family_acceptable": True,
                "outcome_ok": True,
                "no_telemetry_expected_tool": True,
                "needs_review": True,
                "route_families": {"file": 1, "web": 1},
                "routes": {"search_files": 1, "web_search": 1},
            },
        ],
    )
    write_summary(
        second,
        cases=[
            {
                "case": "web-docs",
                "expected_family": "web",
                "session_id": "s3",
                "returncode": 0,
                "event_count": 1,
                "advisor_mismatches": 0,
                "errors": 0,
                "expected_family_events": 1,
                "unexpected_families": [],
                "route_family_ok": True,
                "needs_review": False,
                "route_families": {"web": 1},
                "routes": {"web_extract": 1},
            }
        ],
    )

    summary = report.build_outcome_report([str(tmp_path / "run-*.json")])

    assert summary["run_count"] == 2
    assert summary["case_count"] == 3
    assert summary["event_count"] == 5
    assert summary["mismatch_event_count"] == 1
    assert summary["review_case_count"] == 1
    assert summary["route_family_ok_rate"] == 0.6667
    assert summary["route_family_acceptable_rate"] == 1.0
    assert summary["outcome_ok_rate"] == 1.0
    assert summary["no_telemetry_expected_tool_count"] == 1
    assert summary["timeout_count"] == 0
    assert summary["failure_count"] == 0
    assert summary["needs_review_cases"] == [{
        "case": "file-inspect",
        "source": str(first),
        "expected_family": "file",
        "session_id": "s2",
        "acceptable_families": ["file", "web"],
        "route_family_acceptable": True,
        "outcome_ok": True,
        "no_telemetry_expected_tool": True,
        "timed_out": False,
    }]
    assert summary["unexpected_families"] == {"web": 1}
    assert summary["tool_errors"] == {"search_files": 1, "web_search": 1}
    assert summary["expected_family_stats"]["web"]["cases"] == 2
    assert summary["expected_family_stats"]["web"]["route_family_ok_rate"] == 1.0
    assert summary["expected_family_stats"]["file"]["route_family_acceptable_rate"] == 1.0
    assert summary["expected_family_stats"]["file"]["needs_review_cases"] == 1
    assert summary["case_stability"]["web-docs"] == {
        "runs": 2,
        "route_family_ok_values": [True, True],
        "outcome_ok_values": [True, True],
        "needs_review_values": [False, False],
        "stable_route_family_ok": True,
        "stable_outcome_ok": True,
        "stable_needs_review": True,
    }


def test_json_and_text_output(tmp_path, capsys):
    path = tmp_path / "run.json"
    write_summary(
        path,
        cases=[
            {
                "case": "unstable",
                "expected_family": "lcm",
                "session_id": "s4",
                "returncode": 1,
                "event_count": 0,
                "advisor_mismatches": 0,
                "errors": 0,
                "expected_family_events": 0,
                "unexpected_families": [],
                "route_family_ok": False,
                "needs_review": True,
                "route_families": {},
                "routes": {},
            }
        ],
    )

    assert report.main([str(path), "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["schema_version"] == 1
    assert parsed["run_count"] == 1

    assert report.main([str(path)]) == 0
    text = capsys.readouterr().out
    assert "Context route outcome report" in text
    assert "Runs: 1" in text
    assert "needs_review cases" in text
    assert "expected_family=lcm" in text


def test_missing_input_files_fail_clearly(tmp_path, capsys):
    pattern = str(tmp_path / "missing-*.json")

    with pytest.raises(ValueError, match="no input files matched"):
        report.build_outcome_report([pattern])

    with pytest.raises(SystemExit) as exc_info:
        report.main([pattern])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "no input files matched" in captured.err
    assert pattern in captured.err
