"""Route-aware probe contracts, without SSH, HTTP, DB, or live state access."""
from dataclasses import replace
from unittest.mock import Mock

import pytest

from hermes_cli import honcho_monitor as hm


@pytest.fixture
def collect(monkeypatch, tmp_path):
    monkeypatch.setattr(hm, "_state_path", lambda: tmp_path / "state.json")
    monkeypatch.setattr(hm, "ssh", lambda *args, **kwargs: "")
    monkeypatch.setattr(hm, "latest_local_message_timestamp", lambda: None)
    monkeypatch.setattr(hm, "build_ingestion_status", lambda *args, **kwargs: {})
    monkeypatch.setattr(hm, "collect_recent_deriver_errors", lambda *args, **kwargs: {})

    def run(pipeline, response=(False, {}, 0.2)):
        monkeypatch.setattr(hm, "parse_pipeline_env", lambda *args: pipeline)
        request = Mock(return_value=response)
        monkeypatch.setattr(hm, "curl_post_json", request)
        snapshot, _ = hm.collect_snapshot()
        return snapshot, request

    return run


@pytest.mark.parametrize("pipeline", [{}, {"deriver": {"base_url": "https://cloud.example/v1", "model": "cloud"}}])
def test_unrouted_endpoint_is_not_a_health_dependency(collect, pipeline):
    snapshot, request = collect(pipeline)
    request.assert_not_called()
    assert snapshot.spark_goat["skipped"] is True
    snapshot = replace(snapshot, spark_goat={"skipped": True, "ok": False, "thinking": True, "latency_s": 99})
    alerts = []
    hm._spark_alerts(snapshot, None, alerts)
    assert alerts == []
    assert "⚪ spark-goat chat: not routed (skipped)" in hm.format_report(snapshot)


def test_unknown_report_does_not_imply_spark_dependency(collect):
    snapshot, request = collect({"_routing": {"status": "unknown"}})
    snapshot = replace(snapshot, services={"ssh_ok": True})
    report = hm.format_report(snapshot)
    assert "Runtime routing: unverified (no provider probe)" in report
    assert "spark-goat chat:" not in report
    assert "Honcho routing unknown: effective config sources unverified" in report
    request.assert_not_called()


@pytest.mark.parametrize("stage", ["deriver", "summary", "dream", "dialectic", "embedding"])
@pytest.mark.parametrize("base", ["http://192.168.10.211:8001/v1", "http://100.69.54.37:8001/v1"])
def test_unattested_route_does_not_reactivate_probe(collect, stage, base):
    snapshot, request = collect({stage: {"base_url": base, "model": "served"}})
    request.assert_not_called()
    assert snapshot.spark_goat.get("skipped") is True
    alerts = []
    hm._spark_alerts(snapshot, None, alerts)
    assert "spark-goat chat failed" not in alerts


def test_unattested_route_keeps_selected_model_without_request(collect):
    snapshot, request = collect(
        {"deriver": {"base_url": "http://192.168.10.211:8001/v1", "model": "served"}},
        (True, {"choices": [{"message": {"content": "hi"}}]}, 0.2),
    )
    assert snapshot.spark_goat["ok"] is False
    assert snapshot.spark_goat["model"] == "served"
    request.assert_not_called()
