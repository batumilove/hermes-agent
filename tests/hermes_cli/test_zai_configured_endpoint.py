"""An explicitly routed Z.AI model never needs vendor endpoint discovery."""

import pytest


@pytest.mark.parametrize("provider", ["zai", "glm"])
def test_explicit_model_endpoint_skips_vendor_probe(tmp_path, monkeypatch, provider):
    from hermes_cli import auth
    from hermes_cli.auth_zai_kimi import _resolve_zai_base_url

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(
        f"model:\n  provider: {provider}\n  base_url: http://127.0.0.1:8765/custom\n"
    )

    def unexpected_probe(*args, **kwargs):
        pytest.fail("explicit endpoint must not probe vendor hosts")

    monkeypatch.setattr(auth, "detect_zai_endpoint", unexpected_probe)
    assert _resolve_zai_base_url("test-key", "https://api.z.ai/api/paas/v4", "") == "http://127.0.0.1:8765/custom"


def test_other_provider_endpoint_is_not_used(tmp_path, monkeypatch):
    from hermes_cli import auth
    from hermes_cli.auth_zai_kimi import _resolve_zai_base_url

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    (home / "config.yaml").write_text(
        "model:\n  provider: openai\n  base_url: http://127.0.0.1:8765/other\n"
    )
    probes = []
    monkeypatch.setattr(auth, "detect_zai_endpoint", lambda key: probes.append(key))
    assert _resolve_zai_base_url("other-provider-test-key", "https://api.z.ai/api/paas/v4", "") == "https://api.z.ai/api/paas/v4"
    assert probes == ["other-provider-test-key"]
