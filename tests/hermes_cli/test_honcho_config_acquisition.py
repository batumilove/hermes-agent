"""Metadata transport must not manufacture parser/route completeness."""
import json
import pytest
from hermes_cli import honcho_config_acquisition as acquisition


def packet():
    return {"hostname": "honcho", "containers": {name: {
        "container_id": "a" * 64, "image_id": "sha256:" + "b" * 64,
        "started_at": "2026-10-24T05:24:12Z", "running": True,
        "source_manifest": {"src/config.py": "c" * 64},
        "parser_manifest": {"pydantic_settings/main.py": "d" * 64},
        "source_presence": {"/app/config.toml": False},
        "env_input": {}, "unsupported_input": False,
    } for name in ("api", "deriver")}}


def test_acquires_independent_baseline_and_never_asserts_route():
    calls = []
    def transport(script, **kwargs):
        calls.append(script)
        return json.dumps(packet())
    result = acquisition.acquire_metadata(transport=transport, clock=lambda: 2000)
    assert len(calls) == 2
    assert result["status"] == "UNSUPPORTED"
    assert result["reason"] == "parser_source_closure_not_proven"
    assert result["stable"] is True
    assert result["actual_routing"] == "unknown"
    assert result["receipt"] is None
    assert result["probe_authorized"] is False
    assert set(result["observations"]) == {"api", "deriver"}


def test_canonical_fingerprint():
    assert acquisition.fingerprint({"a": 1, "b": 2}) == acquisition.fingerprint({"b": 2, "a": 1})


@pytest.mark.parametrize("field", ["container_id", "image_id", "started_at", "source_manifest", "parser_manifest", "env_input", "source_presence"])
def test_changes_invalidate_collection(field):
    first, second = packet(), packet()
    second["containers"]["deriver"][field] = {"changed": True} if isinstance(second["containers"]["deriver"][field], dict) else "changed"
    samples = iter([json.dumps(first), json.dumps(second)])
    result = acquisition.acquire_metadata(transport=lambda _, **kwargs: next(samples), clock=lambda: 2000)
    assert result["status"] != "inspected_config_matches"
    assert result["receipt"] is None
    assert result.get("stable") is not True


@pytest.mark.parametrize("raw", ["{", "[]", "x" * 262145])
def test_bad_transport_is_sanitized(raw):
    result = acquisition.acquire_metadata(transport=lambda _, **kwargs: raw, clock=lambda: 2000)
    assert result["reason"] == "metadata_acquisition_failed"
    assert result["observations"] == {}


def test_transport_exception_is_not_reflected():
    def fail(_, **kwargs):
        raise RuntimeError("credential-sentinel")
    assert "credential-sentinel" not in json.dumps(acquisition.acquire_metadata(transport=fail))


def test_elapsed_ttl_rejected():
    stamps = iter([1000, 1301])
    result = acquisition.acquire_metadata(transport=lambda _, **kwargs: json.dumps(packet()), clock=lambda: next(stamps))
    assert result["reason"] == "collection_expired_or_changed"


def test_remote_script_is_metadata_only():
    script = acquisition.REMOTE_SCRIPT
    assert "docker" in script and "inspect" in script
    assert "src.config" not in script and "import pydantic" not in script
    assert "Config.Env" not in script
    assert "-I" in script and "-S" in script
