"""Tests for scripts/langfuse-trace-lookup.py — URL encoding and query building.

These tests exercise the query-parameter construction in ``_fetch_traces``
without requiring real Langfuse credentials.  A lightweight stub intercepts
the HTTP call so we can inspect the generated URL.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "langfuse-trace-lookup.py"

# Import the script as a module (it lives outside any package).
_spec = importlib.util.spec_from_file_location("langfuse_trace_lookup", SCRIPT_PATH)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["langfuse_trace_lookup"] = _mod


@pytest.fixture(autouse=True)
def _load_script():
    """Ensure the module is (re)loaded fresh for each test."""
    sys.modules.pop("langfuse_trace_lookup", None)
    mod = importlib.util.module_from_spec(_spec)
    sys.modules["langfuse_trace_lookup"] = mod
    _spec.loader.exec_module(mod)
    return mod


def _captured_url(mod, **kwargs):
    """Call _fetch_traces with a stubbed _langfuse_get, return the URL."""
    captured = {}

    def fake_get(url, pk, sk):
        captured["url"] = url
        return {"data": []}

    with patch.object(mod, "_langfuse_get", fake_get):
        mod._fetch_traces("https://langfuse.example.com", "pk", "sk", **kwargs)

    return captured["url"]


class TestFetchTracesURLEncoding:
    """Verify that _fetch_traces properly URL-encodes all query parameters."""

    def test_simple_session_id(self, _load_script):
        url = _captured_url(_load_script, session_id="abc123")
        assert "sessionId=abc123" in url

    def test_session_id_with_ampersand(self, _load_script):
        url = _captured_url(_load_script, session_id="foo&bar")
        # & must be encoded as %26 so it doesn't inject a new param.
        assert "sessionId=foo%26bar" in url
        assert "&bar=" not in url

    def test_session_id_with_space(self, _load_script):
        url = _captured_url(_load_script, session_id="hello world")
        # urlencode uses '+' for spaces in query strings (valid per RFC 3986 / HTML forms).
        assert "sessionId=hello+world" in url or "sessionId=hello%20world" in url

    def test_session_id_with_hash(self, _load_script):
        url = _captured_url(_load_script, session_id="sess#1")
        assert "sessionId=sess%231" in url

    def test_session_id_with_equals(self, _load_script):
        url = _captured_url(_load_script, session_id="a=b")
        assert "sessionId=a%3Db" in url

    def test_session_id_with_slash(self, _load_script):
        url = _captured_url(_load_script, session_id="path/to/sess")
        assert "sessionId=path%2Fto%2Fsess" in url

    def test_session_id_with_question_mark(self, _load_script):
        url = _captured_url(_load_script, session_id="what?")
        assert "sessionId=what%3F" in url

    def test_single_tag(self, _load_script):
        url = _captured_url(_load_script, tags=["hermes"])
        assert "tags=hermes" in url

    def test_multiple_tags_repeated_param(self, _load_script):
        """Repeated tags must appear as separate tags=... parameters."""
        url = _captured_url(_load_script, tags=["alpha", "beta"])
        # urlencode with doseq=True produces tags=alpha&tags=beta
        assert "tags=alpha" in url
        assert "tags=beta" in url

    def test_tag_with_reserved_chars(self, _load_script):
        url = _captured_url(_load_script, tags=["platform:telegram", "a&b"])
        assert "tags=platform%3Atelegram" in url
        assert "tags=a%26b" in url

    def test_tag_with_colon_and_equals(self, _load_script):
        url = _captured_url(_load_script, tags=["key=val ue"])
        # '=' is encoded as %3D, space as '+' (urlencode default).
        assert "tags=key%3Dval+ue" in url or "tags=key%3Dval%20ue" in url

    def test_limit_param_present(self, _load_script):
        url = _captured_url(_load_script, session_id="s", limit=5)
        assert "limit=5" in url

    def test_orderby_present(self, _load_script):
        url = _captured_url(_load_script, session_id="s")
        assert "orderBy=timestamp.desc" in url

    def test_from_timestamp(self, _load_script):
        url = _captured_url(_load_script, session_id="s", from_timestamp="2025-01-01T00:00:00Z")
        # + must be encoded; : stays unencoded by default which is fine.
        assert "fromTimestamp=2025-01-01T00%3A00%3A00Z" in url

    def test_combined_params(self, _load_script):
        url = _captured_url(
            _load_script,
            session_id="sess&1",
            tags=["a=b", "c d"],
            limit=20,
        )
        assert "sessionId=sess%261" in url
        assert "tags=a%3Db" in url
        assert "tags=c+d" in url or "tags=c%20d" in url
        assert "limit=20" in url
        assert "orderBy=timestamp.desc" in url

    def test_base_url_not_doubly_slashed(self, _load_script):
        url = _captured_url(_load_script, session_id="x")
        assert url.startswith("https://langfuse.example.com/api/public/traces?")

    def test_empty_session_id_omitted(self, _load_script):
        url = _captured_url(_load_script, tags=["hermes"])
        assert "sessionId" not in url

    def test_no_tags_omitted(self, _load_script):
        url = _captured_url(_load_script, session_id="s")
        assert "tags=" not in url
