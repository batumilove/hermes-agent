import json

from agent.context_efficiency_report import build_report, load_events, summarize_events


def test_load_events_skips_invalid_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"route":"session_search"}\nnot json\n{"route":"memory"}\n', encoding="utf-8")

    events = load_events(path)

    assert [event["route"] for event in events] == ["session_search", "memory"]


def test_summarize_events_groups_routes_and_error_rate():
    summary = summarize_events([
        {"route": "session_search", "duration_s": 0.1, "result_chars": 100, "session_id": "a", "advisor_family": "session_search", "advisor_match": True},
        {"route": "session_search", "duration_s": 0.3, "result_chars": 300, "session_id": "a", "is_error": True, "advisor_family": "web", "advisor_match": False},
        {"route": "memory", "duration_s": 1.0, "result_chars": 50, "session_id": "b"},
    ])

    assert summary["events"] == 3
    by_route = {row["route"]: row for row in summary["routes"]}
    assert by_route["session_search"]["calls"] == 2
    assert by_route["session_search"]["errors"] == 1
    assert by_route["session_search"]["error_rate"] == 0.5
    assert by_route["session_search"]["avg_duration_s"] == 0.2
    assert by_route["session_search"]["avg_result_chars"] == 200.0
    assert by_route["session_search"]["advisor_mismatches"] == 1
    assert by_route["session_search"]["advisor_mismatch_rate"] == 0.5
    assert by_route["memory"]["sessions"] == 1


def test_build_report_includes_source_and_route(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"route": "lcm_expand", "duration_s": 0.2, "result_chars": 10}) + "\n", encoding="utf-8")

    report = build_report(str(path))

    assert f"Source: {path}" in report
    assert "lcm_expand: calls=1" in report
