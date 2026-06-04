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


def test_natural_cases_cover_required_families_and_controls():
    natural = canary.select_cases(["all"], natural=True)
    families = {case.family for case in natural}

    assert len(natural) >= 30
    assert {
        "session_search",
        "durable_memory",
        "current_session_lcm",
        "web",
        "file",
        "no_tool",
    }.issubset(families)
    assert any("ambiguous" in case.name and case.family == "session_search" for case in natural)
    assert any("ambiguous" in case.name and case.family == "file" for case in natural)
    assert any("ambiguous" in case.name and case.family == "web" for case in natural)


def test_natural_ambiguous_cases_declare_realistic_acceptable_families():
    natural = {case.name: case for case in canary.select_cases(["all"], natural=True)}

    assert canary.case_acceptable_families(natural["natural-ambiguous-memory-session"]) == ("session_search", "durable_memory")
    assert canary.case_acceptable_families(natural["natural-ambiguous-preference-current"]) == ("durable_memory", "session_search")
    assert canary.case_acceptable_families(natural["natural-ambiguous-online-docs"]) == ("web", "file")
    assert canary.case_non_blocking_extra_families(natural["natural-file-canary-script"]) == ("session_search",)
    assert "web_search only" in natural["natural-ambiguous-online-docs"].prompt
    assert "do not use web_extract" in natural["natural-ambiguous-online-docs"].prompt


def test_expand_repetitions_preserves_batch_order():
    cases = [
        canary.CanaryCase("web-case", "web", ("web",), "Find current docs URL"),
        canary.CanaryCase("file-case", "file", ("file",), "Find source path"),
    ]

    repeated = canary.expand_repetitions(cases, 2)

    assert [case.name for case in repeated] == ["web-case", "file-case", "web-case", "file-case"]


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
    assert summary["acceptable_family_events"] == 1
    assert summary["unexpected_families"] == ["file"]
    assert summary["advisor_mismatches"] == 1
    assert summary["route_family_ok"] is False
    assert summary["route_family_acceptable"] is False
    assert summary["outcome_ok"] is False
    assert summary["needs_review"] is True


def test_summarize_case_outcome_allows_secondary_acceptable_family():
    case = canary.CanaryCase("docs-case", "web", ("web", "file"), "Find docs", ("web", "file"))
    result = {"returncode": 0, "stdout": "docs", "stderr": "session_id: sess1"}
    events = [
        {"session_id": "sess1", "route": "search_files", "route_family": "file", "advisor_family": "file", "advisor_match": True, "is_error": False},
    ]

    summary = canary.summarize_case_outcome(case, result, events)

    assert summary["acceptable_families"] == ["web", "file"]
    assert summary["expected_family_events"] == 0
    assert summary["acceptable_family_events"] == 1
    assert summary["unexpected_families"] == []
    assert summary["route_family_ok"] is False
    assert summary["route_family_acceptable"] is True
    assert summary["outcome_ok"] is True
    assert summary["needs_review"] is False


def test_summarize_case_outcome_buckets_timeouts_and_failures():
    case = canary.CanaryCase("web-case", "web", ("web",), "Find current docs URL")

    timed = canary.summarize_case_outcome(case, {"returncode": 124, "timed_out": True, "stderr": "session_id: timeout"}, [])
    failed = canary.summarize_case_outcome(case, {"returncode": 2, "stderr": "session_id: fail"}, [])

    assert timed["timed_out"] is True
    assert timed["failed"] is True
    assert timed["outcome_ok"] is False
    assert failed["timed_out"] is False
    assert failed["failed"] is True


def test_summarize_case_outcome_accepts_secondary_family_but_keeps_mismatch_separate():
    case = canary.CanaryCase("natural-ambiguous-memory-session", "session_search", ("memory", "session_search"), "Where did we leave it?", ("durable_memory",))
    result = {"returncode": 0, "stdout": "next step", "stderr": "session_id: sess2"}
    events = [
        {"session_id": "sess2", "route": "honcho_search", "route_family": "durable_memory", "advisor_family": "session_search", "advisor_match": False, "is_error": False},
    ]

    summary = canary.summarize_case_outcome(case, result, events)

    assert summary["acceptable_families"] == ["session_search", "durable_memory"]
    assert summary["route_family_ok"] is False
    assert summary["route_family_acceptable"] is True
    assert summary["outcome_ok"] is True
    assert summary["advisor_mismatches"] == 1
    assert summary["needs_review"] is True


def test_summarize_case_outcome_buckets_timeouts_and_failures():
    case = canary.CanaryCase("web-case", "web", ("web",), "Find current docs URL")

    timed = canary.summarize_case_outcome(case, {"returncode": 124, "timed_out": True, "stderr": "session_id: timeout"}, [])
    failed = canary.summarize_case_outcome(case, {"returncode": 2, "stderr": "session_id: fail"}, [])

    assert timed["timed_out"] is True
    assert timed["failed"] is True
    assert timed["outcome_ok"] is False
    assert failed["timed_out"] is False
    assert failed["failed"] is True


def test_summarize_case_outcome_allows_no_tool_controls_without_events():
    case = canary.CanaryCase("plain", "no_tool", ("web", "file"), "Explain telemetry")
    result = {"returncode": 0, "stdout": "observational only", "stderr": "session_id: sess1", "repetition": 2}

    summary = canary.summarize_case_outcome(case, result, [])

    assert summary["repetition"] == 2
    assert summary["event_count"] == 0
    assert summary["route_family_ok"] is True
    assert summary["route_family_acceptable"] is True
    assert summary["outcome_ok"] is True
    assert summary["needs_review"] is False


def test_summarize_case_outcome_keeps_non_blocking_extra_family_visible_without_failing():
    case = canary.CanaryCase(
        "file-case",
        "file",
        ("file", "session_search"),
        "Find source path",
        (),
        ("session_search",),
    )
    result = {"returncode": 0, "stdout": "path", "stderr": "session_id: sess3"}
    events = [
        {"session_id": "sess3", "route": "search_files", "route_family": "file", "advisor_family": "file", "advisor_match": True, "is_error": False},
        {"session_id": "sess3", "route": "session_search", "route_family": "session_search", "advisor_family": "file", "advisor_match": False, "is_error": False},
    ]

    summary = canary.summarize_case_outcome(case, result, events)

    assert summary["unexpected_families"] == ["session_search"]
    assert summary["blocking_unexpected_families"] == []
    assert summary["advisor_mismatches"] == 1
    assert summary["blocking_advisor_mismatches"] == 0
    assert summary["route_family_ok"] is True
    assert summary["outcome_ok"] is True
    assert summary["needs_review"] is False


def test_summarize_case_outcome_flags_expected_tool_with_zero_telemetry():
    case = canary.CanaryCase("file-case", "file", ("file",), "Find source path")
    result = {"returncode": 0, "stdout": "path", "stderr": "session_id: sess4"}

    summary = canary.summarize_case_outcome(case, result, [])

    assert summary["no_telemetry_expected_tool"] is True
    assert summary["route_family_acceptable"] is False
    assert summary["outcome_ok"] is False
    assert summary["needs_review"] is True


def test_summarize_batch_run_groups_appended_events_by_case_session_and_repeat():
    cases = [
        canary.CanaryCase("web-case", "web", ("web",), "Find current docs URL"),
        canary.CanaryCase("file-case", "file", ("file",), "Find source path"),
        canary.CanaryCase("web-case", "web", ("web",), "Find current docs URL"),
        canary.CanaryCase("file-case", "file", ("file",), "Find source path"),
    ]
    results = [
        {"returncode": 0, "stdout": "url", "stderr": "session_id: s1", "repetition": 1},
        {"returncode": 0, "stdout": "path", "stderr": "session_id: s2", "repetition": 1},
        {"returncode": 0, "stdout": "url", "stderr": "session_id: s3", "repetition": 2},
        {"returncode": 0, "stdout": "path", "stderr": "session_id: s4", "repetition": 2},
    ]
    appended = [
        {"session_id": "s1", "route": "web_search", "route_family": "web", "advisor_family": "web", "advisor_match": True},
        {"session_id": "s2", "route": "search_files", "route_family": "file", "advisor_family": "file", "advisor_match": True},
        {"session_id": "s3", "route": "web_search", "route_family": "web", "advisor_family": "web", "advisor_match": True},
        {"session_id": "s4", "route": "search_files", "route_family": "file", "advisor_family": "file", "advisor_match": True},
    ]

    summary = canary.summarize_batch_run(
        profile="tdai-canary",
        cases=cases,
        results=results,
        appended=appended,
        log_path=Path("events.jsonl"),
        before=10,
        after=14,
        natural=True,
        repeat=2,
    )

    assert summary["schema_version"] == 1
    assert summary["repeat"] == 2
    assert summary["case_count"] == 4
    assert summary["event_count"] == 4
    assert summary["review_case_count"] == 0
    assert summary["route_family_acceptable_count"] == 4
    assert summary["outcome_ok_count"] == 4
    assert summary["timeout_count"] == 0
    assert summary["failure_count"] == 0
    assert [case["session_id"] for case in summary["cases"]] == ["s1", "s2", "s3", "s4"]
    assert [case["repetition"] for case in summary["cases"]] == [1, 1, 2, 2]
