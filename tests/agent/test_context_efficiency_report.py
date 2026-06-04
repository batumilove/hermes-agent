import json

from agent.context_efficiency_report import build_report, filter_events, load_events, summarize_events, format_summary


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
    assert summary["advisor"]["events"] == 2
    assert summary["advisor"]["mismatches"] == 1
    assert summary["advisor"]["mismatch_rate"] == 0.5
    assert summary["advisor"]["by_family"]["web"]["mismatches"] == 1
    by_family = {row["route_family"]: row for row in summary["families"]}
    assert by_family["session_search"]["calls"] == 2
    assert by_family["durable_memory"]["calls"] == 1


def test_filter_events_supports_since_family_and_mismatches_only():
    events = [
        {"ts": 10, "route": "session_search", "route_family": "session_search", "advisor_family": "web", "advisor_match": False},
        {"ts": 20, "route": "web_search", "route_family": "web", "advisor_family": "web", "advisor_match": True},
        {"ts": 30, "route": "read_file", "route_family": "file", "advisor_family": "file", "advisor_match": True},
    ]

    assert [e["route"] for e in filter_events(events, since=15)] == ["web_search", "read_file"]
    assert [e["route"] for e in filter_events(events, family="web")] == ["session_search", "web_search"]
    assert [e["route"] for e in filter_events(events, mismatches_only=True)] == ["session_search"]


def test_filter_events_derives_family_for_older_events_without_route_family():
    events = [
        {"route": "lcm_grep", "advisor_family": "current_session_lcm", "advisor_match": True},
        {"route": "session_search", "advisor_family": "web", "advisor_match": False},
    ]

    assert [e["route"] for e in filter_events(events, family="current_session_lcm")] == ["lcm_grep"]
    summary = summarize_events(events)
    by_family = {row["route_family"]: row for row in summary["families"]}
    assert by_family["current_session_lcm"]["calls"] == 1
    assert by_family["session_search"]["calls"] == 1


def test_build_report_supports_json_and_filters(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"ts": 10, "route": "session_search", "route_family": "session_search", "advisor_family": "web", "advisor_match": False}),
            json.dumps({"ts": 20, "route": "web_search", "route_family": "web", "advisor_family": "web", "advisor_match": True}),
        ]) + "\n",
        encoding="utf-8",
    )

    report = build_report(str(path), family="web", mismatches_only=True)
    assert "Filters: family=web, mismatches_only=true" in report
    assert "session_search: calls=1" in report
    assert "web_search" not in report

    data = json.loads(build_report(str(path), since=15, json_output=True))
    assert data["events"] == 1
    assert data["source"] == str(path)


def test_format_summary_includes_advisor_rollup_and_mismatch_families():
    text = format_summary({
        "events": 2,
        "advisor": {
            "events": 2,
            "mismatches": 1,
            "mismatch_rate": 0.5,
            "by_family": {"web": {"events": 1, "mismatches": 1, "routes": {"session_search": 1}}},
        },
        "routes": [
            {
                "route": "session_search",
                "calls": 2,
                "errors": 0,
                "avg_duration_s": 0.2,
                "avg_result_chars": 100,
                "sessions": 1,
                "advisor_mismatches": 1,
                "advisor_mismatch_rate": 0.5,
            }
        ],
    })

    assert "Advisor: events=2, mismatches=1 (0.5)" in text
    assert "advisor_family=web: events=1, mismatches=1, top_actual_routes=session_search:1" in text


def test_build_report_includes_source_and_route(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"route": "lcm_expand", "duration_s": 0.2, "result_chars": 10}) + "\n", encoding="utf-8")

    report = build_report(str(path))

    assert f"Source: {path}" in report
    assert "lcm_expand: calls=1" in report
