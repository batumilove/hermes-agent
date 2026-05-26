"""Tests for TinyFish web search + fetch provider."""
from __future__ import annotations

import pytest


class DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_tinyfish_provider_available_with_api_key(monkeypatch):
    from plugins.web.tinyfish.provider import TinyFishWebSearchProvider

    monkeypatch.setenv("TINYFISH_API_KEY", "tf-test")

    provider = TinyFishWebSearchProvider()
    assert provider.name == "tinyfish"
    assert provider.display_name == "TinyFish"
    assert provider.is_available() is True
    assert provider.supports_search() is True
    assert provider.supports_extract() is True
    assert provider.supports_crawl() is False


def test_tinyfish_search_normalizes_results(monkeypatch):
    from plugins.web.tinyfish import provider as tinyfish_provider

    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return DummyResponse(
            {
                "results": [
                    {
                        "title": "TinyFish docs",
                        "url": "https://docs.tinyfish.ai/",
                        "snippet": "Developer docs",
                    },
                    {
                        "title": "Cookbook",
                        "url": "https://github.com/tinyfish-io/tinyfish-cookbook",
                        "description": "Examples",
                    },
                ]
            }
        )

    monkeypatch.setenv("TINYFISH_API_KEY", "tf-test")
    monkeypatch.setattr(tinyfish_provider.httpx, "get", fake_get)
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)

    result = tinyfish_provider.TinyFishWebSearchProvider().search("agent web fetch", limit=2)

    assert captured["url"] == "https://api.search.tinyfish.ai"
    assert captured["params"] == {"query": "agent web fetch"}
    assert captured["headers"] == {"X-API-Key": "tf-test"}
    assert result == {
        "success": True,
        "data": {
            "web": [
                {
                    "title": "TinyFish docs",
                    "url": "https://docs.tinyfish.ai/",
                    "description": "Developer docs",
                    "position": 1,
                },
                {
                    "title": "Cookbook",
                    "url": "https://github.com/tinyfish-io/tinyfish-cookbook",
                    "description": "Examples",
                    "position": 2,
                },
            ]
        },
    }


def test_tinyfish_extract_normalizes_results_and_errors(monkeypatch):
    from plugins.web.tinyfish import provider as tinyfish_provider

    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return DummyResponse(
            {
                "results": [
                    {
                        "url": "https://example.com/a",
                        "final_url": "https://example.com/a?ref=1",
                        "title": "Example A",
                        "description": "Desc",
                        "language": "en",
                        "text": "# Example A\n\nClean content",
                    }
                ],
                "errors": [
                    {"url": "https://example.com/b", "error": "Timeout"}
                ],
            }
        )

    monkeypatch.setenv("TINYFISH_API_KEY", "tf-test")
    monkeypatch.setattr(tinyfish_provider.httpx, "post", fake_post)
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)

    result = tinyfish_provider.TinyFishWebSearchProvider().extract(
        ["https://example.com/a", "https://example.com/b"],
        format="markdown",
    )

    assert captured["url"] == "https://api.fetch.tinyfish.ai"
    assert captured["json"] == {"urls": ["https://example.com/a", "https://example.com/b"], "format": "markdown"}
    assert captured["headers"] == {"X-API-Key": "tf-test", "Content-Type": "application/json"}
    assert result == [
        {
            "url": "https://example.com/a",
            "title": "Example A",
            "content": "# Example A\n\nClean content",
            "raw_content": "# Example A\n\nClean content",
            "metadata": {
                "sourceURL": "https://example.com/a",
                "finalURL": "https://example.com/a?ref=1",
                "title": "Example A",
                "description": "Desc",
                "language": "en",
            },
        },
        {
            "url": "https://example.com/b",
            "title": "",
            "content": "",
            "raw_content": "",
            "error": "Timeout",
            "metadata": {"sourceURL": "https://example.com/b"},
        },
    ]


def test_tinyfish_requires_api_key(monkeypatch):
    from plugins.web.tinyfish.provider import TinyFishWebSearchProvider

    monkeypatch.delenv("TINYFISH_API_KEY", raising=False)

    provider = TinyFishWebSearchProvider()
    assert provider.search("docs")["error"].startswith("TINYFISH_API_KEY")
    assert provider.extract(["https://example.com"])[0]["error"].startswith("TINYFISH_API_KEY")


def test_web_backend_selection_accepts_tinyfish(monkeypatch):
    from tools import web_tools

    monkeypatch.setattr(web_tools, "_load_web_config", lambda: {"backend": "tinyfish"})
    monkeypatch.setenv("TINYFISH_API_KEY", "tf-test")

    assert web_tools._get_backend() == "tinyfish"
    assert web_tools._get_search_backend() == "tinyfish"
    assert web_tools._get_extract_backend() == "tinyfish"
    assert web_tools._is_backend_available("tinyfish") is True
