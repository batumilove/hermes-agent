"""Content-free inspected-config receipts never attest loaded/request routing."""
import copy
import json
import pytest
from hermes_cli import honcho_monitor as monitor

H = "a" * 64
NOW = 2000


def fixture():
    container = {
        "container_id": H, "image_id": "sha256:" + H, "started_at": 1000,
        "source_manifest_sha256": H, "settings_sha256": H,
        "parser_manifest_sha256": H, "source_inputs_sha256": H,
        "env_allowlist_sha256": H, "config_fingerprint": H,
        "routes_fingerprint": H, "router_enabled": False,
    }
    current = {name: copy.deepcopy(container) for name in ("api", "deriver")}
    policy = {"version": 1, "containers": copy.deepcopy(current)}
    receipt = {"schema_version": 1, "scope": "inspected_config_only",
               "collected_at": NOW, "policy_version": 1,
               "containers": copy.deepcopy(current)}
    return receipt, current, policy


def evaluate(receipt=None, current=None, policy=None, now=NOW):
    r, c, p = fixture()
    return monitor.collect_config_attestation(
        json.dumps(r if receipt is None else receipt),
        c if current is None else current, p if policy is None else policy,
        now=now,
    )


def test_inspected_config_separate_from_natural_dispatch():
    result = evaluate()
    assert result == {"configuration": "inspected_config_matches", "actual_routing": "unknown",
                      "reason": "no_natural_dispatch_receipt", "probe_authorized": False}


@pytest.mark.parametrize("field", list(fixture()[0]["containers"]["api"]))
@pytest.mark.parametrize("name", ["api", "deriver"])
def test_every_identity_and_fingerprint_is_bound(field, name):
    r, c, p = fixture()
    c[name][field] = True if field == "router_enabled" else (1001 if field == "started_at" else "b" * 64)
    assert evaluate(r, c, p)["configuration"] == "unknown"


@pytest.mark.parametrize("mutation", ["missing", "stale", "future", "malformed", "secret", "route", "enabled", "version", "scope", "partial"])
def test_fail_closed(mutation):
    r, c, p = fixture()
    if mutation == "missing": r = {}
    if mutation == "stale": r["collected_at"] = NOW - 301
    if mutation == "future": r["collected_at"] = NOW + 1
    if mutation == "malformed": r["collected_at"] = "today"
    if mutation == "secret": r["containers"]["api"]["api_key"] = "credential-sentinel"
    if mutation == "route": p["containers"]["api"]["routes_fingerprint"] = "b" * 64
    if mutation == "enabled": r["containers"]["deriver"]["router_enabled"] = True
    if mutation == "version": r["schema_version"] = 2
    if mutation == "scope": r["scope"] = "effective_loaded_settings"
    if mutation == "partial": del r["containers"]["deriver"]
    result = evaluate(r, c, p)
    assert result["configuration"] == "unknown"
    assert result["actual_routing"] == "unknown"
    assert "credential-sentinel" not in json.dumps(result)
    assert not result["probe_authorized"]


@pytest.mark.parametrize("raw", ["{", "[]", "null", '{"schema_version":1,"schema_version":1}', "x" * 65537])
def test_bad_wire_input(raw):
    _, c, p = fixture()
    assert monitor.collect_config_attestation(raw, c, p, now=NOW)["configuration"] == "unknown"


def test_report_keeps_service_health_and_routing_separate():
    report = monitor.format_report({
        "services": {"api_ok": True, "deriver_up": True, "db_ok": True, "redis_ok": True},
        "pipeline": {"_config_attestation": evaluate()},
        "spark_goat": {"skipped": True},
    })
    assert "🟢 API" in report
    assert "Inspected configuration: UNKNOWN (fresh receipt not collected)" in report
    assert "Actual request routing: UNKNOWN (no natural dispatch receipt)" in report
    assert "inspected_config_matches" not in report  # cached mappings cannot green this


@pytest.mark.parametrize("name", ["api", "deriver"])
def test_receipt_tampering(name):
    r, c, p = fixture()
    r["containers"][name]["config_fingerprint"] = "b" * 64
    assert evaluate(r, c, p)["configuration"] == "unknown"


@pytest.mark.parametrize("age", [0, 300])
def test_ttl_boundary(age):
    r, c, p = fixture()
    r["collected_at"] = NOW - age
    assert evaluate(r, c, p)["configuration"] == "inspected_config_matches"


def test_no_io_or_provider_or_database(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("transport/state forbidden")
    monkeypatch.setattr(monitor, "ssh", forbidden)
    monkeypatch.setattr(monitor.sqlite3, "connect", forbidden)
    monkeypatch.setattr(monitor.subprocess, "run", forbidden)
    assert evaluate()["configuration"] == "inspected_config_matches"
    assert monitor.spark_probe_authorized({"_config_attestation": evaluate()}) is False


@pytest.mark.parametrize("now", [True, float("nan"), float("inf"), -1])
def test_invalid_clock(now):
    assert evaluate(now=now)["configuration"] == "unknown"
