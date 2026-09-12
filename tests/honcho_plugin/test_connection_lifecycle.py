"""Contracts for deterministic Honcho HTTP connection ownership."""

from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import patch

import httpx
import pytest

from plugins.memory.honcho import client as honcho_client
from plugins.memory.honcho.client import (
    HonchoClientConfig,
    get_honcho_client,
    reset_honcho_client,
)


class _OwnedHTTPClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class _HTTPWrapper:
    def __init__(self, inner: _OwnedHTTPClient):
        self._client = inner
        self._owns_client = False

    def close(self) -> None:
        self._client.close()


def _sdk_module(*, ignore_injected_client: bool = False, fail_build: bool = False):
    module = types.ModuleType("honcho")

    class FakeHoncho:
        def __init__(self, **kwargs):
            if fail_build:
                raise RuntimeError("build failed")
            inner = object() if ignore_injected_client else kwargs["http_client"]
            self._http = _HTTPWrapper(inner)
            self._async_http = None

        @property
        def _async_http_client(self):  # pragma: no cover - must remain untouched
            raise AssertionError("reset allocated the lazy async client")

    module.Honcho = FakeHoncho
    return module


@pytest.fixture(autouse=True)
def _isolated_singleton():
    honcho_client._honcho_client_slot.reset()
    honcho_client._cached_timeout = None
    honcho_client._honcho_json_timeout_memo = (None, None)
    yield
    honcho_client._honcho_client_slot.reset()
    honcho_client._cached_timeout = None
    honcho_client._honcho_json_timeout_memo = (None, None)


def _config(timeout: float = 11.0) -> HonchoClientConfig:
    return HonchoClientConfig(
        api_key="test-key",
        workspace_id="test-workspace",
        environment="production",
        timeout=timeout,
    )


def test_build_disables_idle_keepalive_and_reset_closes_owned_client(monkeypatch):
    created: list[_OwnedHTTPClient] = []
    limits: list[dict] = []

    def build_http_client(**kwargs):
        client = _OwnedHTTPClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", build_http_client)
    monkeypatch.setattr(httpx, "Limits", lambda **kwargs: limits.append(kwargs) or kwargs)
    monkeypatch.setattr(httpx, "Timeout", lambda value: value)

    with patch.dict(sys.modules, {"honcho": _sdk_module()}), patch(
        "plugins.memory.honcho.client._apply_fresh_oauth_token"
    ), patch("hermes_cli.config.load_config", return_value={}):
        client = get_honcho_client(_config())
        reset_honcho_client()

    assert limits == [{"max_keepalive_connections": 0, "max_connections": 100}]
    assert client._http._owns_client is True
    assert created[0].kwargs["timeout"] == 11.0
    assert created[0].close_count == 1


def test_timeout_replacement_closes_previous_client(monkeypatch):
    created: list[_OwnedHTTPClient] = []

    def build_http_client(**kwargs):
        client = _OwnedHTTPClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", build_http_client)
    monkeypatch.setattr(httpx, "Limits", lambda **kwargs: kwargs)
    monkeypatch.setattr(httpx, "Timeout", lambda value: value)

    with patch.dict(sys.modules, {"honcho": _sdk_module()}), patch(
        "plugins.memory.honcho.client._apply_fresh_oauth_token"
    ), patch("hermes_cli.config.load_config", return_value={}):
        first = get_honcho_client(_config(10.0))
        second = get_honcho_client(_config(20.0))

    assert first is not second
    assert [client.close_count for client in created] == [1, 0]


@pytest.mark.parametrize("failure", [RuntimeError("build failed"), KeyboardInterrupt()])
def test_failed_sdk_construction_closes_injected_client(monkeypatch, failure):
    created: list[_OwnedHTTPClient] = []

    def build_http_client(**kwargs):
        client = _OwnedHTTPClient(**kwargs)
        created.append(client)
        return client

    class FailingHoncho:
        def __init__(self, **kwargs):
            raise failure

    sdk = types.ModuleType("honcho")
    sdk.Honcho = FailingHoncho
    monkeypatch.setattr(httpx, "Client", build_http_client)

    with patch.dict(sys.modules, {"honcho": sdk}), patch(
        "plugins.memory.honcho.client._apply_fresh_oauth_token"
    ), patch("hermes_cli.config.load_config", return_value={}):
        with pytest.raises(type(failure), match=str(failure)):
            get_honcho_client(_config())

    assert created[0].close_count == 1


def test_ignored_injected_client_is_closed_immediately(monkeypatch):
    owned = _OwnedHTTPClient()
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: owned)

    with patch.dict(
        sys.modules, {"honcho": _sdk_module(ignore_injected_client=True)}
    ), patch("plugins.memory.honcho.client._apply_fresh_oauth_token"), patch(
        "hermes_cli.config.load_config", return_value={}
    ):
        client = get_honcho_client(_config())

    assert client._http._owns_client is False
    assert owned.close_count == 1


def test_reset_closes_existing_async_backing_without_allocating_it(monkeypatch):
    class AsyncHTTP:
        _owns_client = True

        def __init__(self):
            self.close_count = 0

        async def close(self):
            self.close_count += 1

    monkeypatch.setattr(httpx, "Client", _OwnedHTTPClient)

    with patch.dict(sys.modules, {"honcho": _sdk_module()}), patch(
        "plugins.memory.honcho.client._apply_fresh_oauth_token"
    ), patch("hermes_cli.config.load_config", return_value={}):
        client = get_honcho_client(_config())
        async_http = AsyncHTTP()
        client._async_http = async_http
        reset_honcho_client()

    assert async_http.close_count == 1


def test_reset_detaches_singleton_when_close_fails(monkeypatch, caplog):
    class FailingHTTPClient(_OwnedHTTPClient):
        def close(self):
            raise RuntimeError("close failed")

    monkeypatch.setattr(httpx, "Client", FailingHTTPClient)

    with patch.dict(sys.modules, {"honcho": _sdk_module()}), patch(
        "plugins.memory.honcho.client._apply_fresh_oauth_token"
    ), patch("hermes_cli.config.load_config", return_value={}):
        get_honcho_client(_config())
        with caplog.at_level("WARNING"):
            reset_honcho_client()

    assert "Honcho sync HTTP client close failed" in caplog.text
    assert honcho_client._honcho_client_slot.peek() is None
    assert honcho_client._cached_timeout is None
    assert honcho_client._honcho_json_timeout_memo == (None, None)


def test_running_loop_observes_async_close_failure(monkeypatch, caplog):
    class AsyncHTTP:
        _owns_client = True

        async def close(self):
            raise RuntimeError("async close failed")

    monkeypatch.setattr(httpx, "Client", _OwnedHTTPClient)

    async def exercise():
        with patch.dict(sys.modules, {"honcho": _sdk_module()}), patch(
            "plugins.memory.honcho.client._apply_fresh_oauth_token"
        ), patch("hermes_cli.config.load_config", return_value={}):
            client = get_honcho_client(_config())
            client._async_http = AsyncHTTP()
            reset_honcho_client()
            await asyncio.sleep(0)

    with caplog.at_level("WARNING"):
        asyncio.run(exercise())

    assert "Honcho async client close failed" in caplog.text
    assert "async close failed" in caplog.text
