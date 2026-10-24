"""HTTP ownership contracts for the reviewed overlay on the exact catalog Honcho pin."""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures" / "honcho-e2d2aba"


@pytest.fixture
def client_module(tmp_path, monkeypatch):
    overlay = json.loads((ROOT / "patches/external-plugins/overlays.json").read_text())["overlays"][0]
    source = json.loads((FIXTURE / "SOURCE.json").read_text())
    assert source["commit"] == overlay["commit"]
    destination = tmp_path / "honcho"
    shutil.copytree(FIXTURE, destination)
    for name, expected in overlay["source_sha256"].items():
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected
    patch = ROOT / overlay["patch"]
    assert hashlib.sha256(patch.read_bytes()).hexdigest() == overlay["sha256"]
    subprocess.run(["git", "apply", "--check", str(patch)], cwd=destination, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=destination, check=True)
    package = types.ModuleType("frozen_honcho")
    package.__path__ = [str(destination)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    # Each test exercises a fresh import; no cache slot or async task crosses the fixture.
    for name in list(sys.modules):
        if name.startswith("frozen_honcho."):
            monkeypatch.delitem(sys.modules, name)
    module = importlib.import_module("frozen_honcho.client")
    monkeypatch.setattr(module, "_refresh_oauth", lambda *args, **kwargs: None)
    monkeypatch.setattr("hermes_cli.config.load_config", dict)
    yield module
    module.reset_honcho_client()


class HTTPWrapper:
    def __init__(self, client):
        self._client = client
        self._owns_client = False
        self.close_count = 0

    def close(self):
        self.close_count += 1
        self._client.close()


@pytest.fixture
def fake_sdk(monkeypatch):
    module = types.ModuleType("honcho")

    class Honcho:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._http = HTTPWrapper(kwargs["http_client"])
            self._async_http = None

        @property
        def _async_http_client(self):
            raise AssertionError("cleanup allocated the lazy async transport")

    module.Honcho = Honcho
    monkeypatch.setitem(sys.modules, "honcho", module)
    return module


def config(module, timeout=11.0, **kwargs):
    return module.HonchoClientConfig(
        api_key="test-key", workspace_id="test-workspace", environment="production", timeout=timeout, **kwargs,
    )


def test_reset_closes_owned_cached_client_once(client_module, fake_sdk):
    client = client_module.get_honcho_client(config(client_module))
    assert client._http._owns_client
    client_module.reset_honcho_client()
    client_module.reset_honcho_client()
    assert client._http.close_count == 1
    assert client._http._client.is_closed


def test_timeout_replacement_closes_old_client(client_module, fake_sdk):
    old = client_module.get_honcho_client(config(client_module, 10.0))
    new = client_module.get_honcho_client(config(client_module, 20.0))
    assert old is not new
    assert old._http.close_count == 1
    assert old._http._client.is_closed
    assert not new._http._client.is_closed


def test_custom_http_client_keeps_no_idle_connections(client_module, fake_sdk):
    client = client_module.get_honcho_client(config(client_module))
    assert client.kwargs["http_client"]._transport._pool._max_keepalive_connections == 0


@pytest.mark.asyncio
async def test_reset_closes_existing_async_backing_without_allocating(client_module, fake_sdk):
    class AsyncHTTP:
        _owns_client = True
        close_count = 0

        async def close(self):
            self.close_count += 1

    client = client_module.get_honcho_client(config(client_module))
    backing = AsyncHTTP()
    client._async_http = backing
    client_module.reset_honcho_client()
    await asyncio.gather(*client_module._pending_async_close_tasks)
    assert backing.close_count == 1


def test_initialization_redacts_base_url_credentials(client_module, fake_sdk, caplog):
    with caplog.at_level("INFO"):
        client_module.get_honcho_client(config(
            client_module, base_url="https://user:secret@example.com/service?api_key=private-token",
        ))
    assert "secret" not in caplog.text
    assert "private-token" not in caplog.text
    assert "https://example.com/service" in caplog.text


def test_failed_construction_closes_injected_client(client_module, fake_sdk, monkeypatch):
    captured = []

    def fail(**kwargs):
        captured.append(kwargs["http_client"])
        raise RuntimeError("SDK construction failed")

    fake_sdk.Honcho = fail
    with pytest.raises(RuntimeError, match="SDK construction failed"):
        client_module.get_honcho_client(config(client_module))
    assert captured[0].is_closed


def test_cleanup_preserves_borrowed_transports(client_module, fake_sdk):
    client = client_module.get_honcho_client(config(client_module))
    client._http._owns_client = False
    client_module.reset_honcho_client()
    assert client._http.close_count == 0
    client._http._client.close()


@pytest.mark.parametrize(("url", "expected"), [
    ("https://user:pass@example.com:8443/api?token=secret#key", "https://example.com:8443/api"),
    ("http://user:pass@[2001:db8::1]:8000/v1?q=secret", "http://[2001:db8::1]:8000/v1"),
    ("not a valid URL?token=secret", "[redacted-url]"),
])
def test_redaction_handles_ipv6_and_malformed_input(client_module, url, expected):
    assert client_module._redact_base_url(url) == expected


def test_explicit_config_captures_profile_provenance(client_module, tmp_path):
    path = tmp_path / "honcho.json"
    home = tmp_path / "profile"
    bound = config(client_module, config_path=path, hermes_home=home)
    assert bound.bound_config_path() == path
    assert bound.hermes_home == home
