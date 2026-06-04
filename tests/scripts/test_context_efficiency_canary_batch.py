import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "context_efficiency_canary_batch.py"
spec = importlib.util.spec_from_file_location("context_efficiency_canary_batch", SCRIPT)
assert spec is not None and spec.loader is not None
canary = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = canary
spec.loader.exec_module(canary)


def test_extract_session_id_from_stderr():
    assert canary.extract_session_id({"stderr": "session_id: 20260604_abc"}) == "20260604_abc"
    assert canary.extract_session_id({"stderr": "no session"}) == ""


def test_summarize_case_outcome_flags_route_family_and_mismatch_review():
    case = canary.CanaryCase("web-case", "web", ("web",), "Find current docs URL")
    result = {"returncode": 0, "stdout": "https://example.test", "stderr": "session_id: sess1"}
    events = [
        {"session_id": "sess1", "route": "web_search", "route_family": "web", "advisor_family": "web", "advisor_match": True, "is_error": False},
        {"session_id": "sess1", "route": "read_file", "route_family": "file", "advisor_family": "web", "advisor_match": False, "is_error": False},
    ]

    summary = canary.summarize_case_outcome(case, result, events)

    assert summary["session_id"] == "sess1"
    assert summary["event_count"] == 2
    assert summary["expected_family_events"] == 1
    assert summary["unexpected_families"] == ["file"]
    assert summary["advisor_mismatches"] == 1
    assert summary["route_family_ok"] is False
    assert summary["needs_review"] is True


def test_summarize_batch_run_groups_appended_events_by_case_session():
    cases = [
        canary.CanaryCase("web-case", "web", ("web",), "Find current docs URL"),
        canary.CanaryCase("file-case", "file", ("file",), "Find source path"),
    ]
    results = [
        {"returncode": 0, "stdout": "url", "stderr": "session_id: s1"},
        {"returncode": 0, "stdout": "path", "stderr": "session_id: s2"},
    ]
    appended = [
        {"session_id": "s1", "route": "web_search", "route_family": "web", "advisor_family": "web", "advisor_match": True},
        {"session_id": "s2", "route": "search_files", "route_family": "file", "advisor_family": "file", "advisor_match": True},
    ]

    summary = canary.summarize_batch_run(
        profile="tdai-canary",
        cases=cases,
        results=results,
        appended=appended,
        log_path=Path("events.jsonl"),
        before=10,
        after=12,
        natural=True,
    )

    assert summary["schema_version"] == 1
    assert summary["case_count"] == 2
    assert summary["event_count"] == 2
    assert summary["review_case_count"] == 0
    assert [case["session_id"] for case in summary["cases"]] == ["s1", "s2"]
