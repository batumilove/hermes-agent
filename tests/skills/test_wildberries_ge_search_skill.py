"""Tests for the wildberries-ge-search optional skill.

No live network calls and no real credentials. Covers the skill contract,
GEL normalization, request shape, HTTP 498 handling, and token-file safety.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[2]
    / "optional-skills"
    / "productivity"
    / "wildberries-ge-search"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
SCRIPT = SKILL_DIR / "scripts" / "wb_ge.py"
BOOTSTRAP = SKILL_DIR / "references" / "token-bootstrap.md"


def load_module():
    spec = importlib.util.spec_from_file_location("wb_ge", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return io.StringIO(json.dumps(self.payload))

    def __exit__(self, *_args):
        return False


def test_skill_files_and_frontmatter():
    assert SKILL_MD.is_file()
    assert SCRIPT.is_file()
    assert BOOTSTRAP.is_file()
    text = SKILL_MD.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert re.search(r"^name: wildberries-ge-search$", text, re.MULTILINE)
    assert re.search(r"^version: 1\.0\.0$", text, re.MULTILINE)
    match = re.search(r"^description: (.*)$", text, re.MULTILINE)
    assert match
    description = match.group(1).strip()
    assert len(description) <= 60
    assert description.endswith(".")


def test_required_sections_and_security_contract():
    text = SKILL_MD.read_text(encoding="utf-8")
    for heading in (
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Procedure",
        "## Security",
        "## Pitfalls",
        "## Verification",
    ):
        assert heading in text
    assert "0600" in text
    assert "HTTP 498" in text
    assert "x_wbaas_token" in text
    assert "payload" in text


def test_skill_tree_has_only_expected_source_files():
    files = {
        path.relative_to(SKILL_DIR).as_posix()
        for path in SKILL_DIR.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }
    assert files == {
        "SKILL.md",
        "references/token-bootstrap.md",
        "scripts/wb_ge.py",
    }


def test_normalizes_gel_price_without_signed_payload():
    mod = load_module()
    product = {
        "id": 123,
        "brand": "LEGO",
        "name": "Set",
        "reviewRating": 4.9,
        "feedbacks": 17,
        "sizes": [
            {
                "price": {"product": 21319, "basic": 36444},
                "payload": "must-not-escape",
            }
        ],
    }
    got = mod.normalized_product(product)
    assert got["price_gel"] == 213.19
    assert got["original_price_gel"] == 364.44
    assert "payload" not in got
    assert "must-not-escape" not in json.dumps(got)


def test_mode_600_token_file_is_accepted(tmp_path):
    mod = load_module()
    token_file = tmp_path / "token"
    token_file.write_text("example-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    args = argparse.Namespace(token_file=str(token_file))
    assert mod.token_from(args) == "example-token"


def test_broad_token_file_permissions_are_rejected_without_value_or_path(tmp_path):
    token_file = tmp_path / "do-not-print-path"
    token_file.write_text("do-not-print-value", encoding="utf-8")
    token_file.chmod(0o644)
    args = argparse.Namespace(token_file=str(token_file))
    with pytest.raises(SystemExit) as caught:
        load_module().token_from(args)
    message = str(caught.value)
    assert "0600" in message
    assert "do-not-print-value" not in message
    assert "do-not-print-path" not in message


def test_environment_token_is_supported(monkeypatch):
    mod = load_module()
    monkeypatch.setenv("WB_X_WBAAS_TOKEN", "env-token")
    assert mod.token_from(argparse.Namespace(token_file=None)) == "env-token"


def test_token_with_control_characters_is_rejected_without_value(monkeypatch):
    token = "do-not-print\r\nInjected: value"
    monkeypatch.setenv("WB_X_WBAAS_TOKEN", token)
    with pytest.raises(SystemExit) as caught:
        load_module().token_from(argparse.Namespace(token_file=None))
    message = str(caught.value)
    assert "control characters" in message
    assert token not in message


def test_http_498_is_clear_and_does_not_leak_token():
    mod = load_module()
    error = urllib.error.HTTPError(
        "https://example.invalid", 498, "blocked", Message(), None
    )
    with patch.object(mod.urllib.request, "urlopen", side_effect=error):
        with pytest.raises(SystemExit) as caught:
            mod.search("lego", 1, "popular", "do-not-print-this")
    text = str(caught.value)
    assert "HTTP 498" in text
    assert "do-not-print-this" not in text


def test_response_contract_accepts_zero_results():
    payload = {"metadata": {}, "products": [], "total": 0}
    assert load_module().validate_response(payload) is payload


@pytest.mark.parametrize(
    "payload",
    [
        {"products": [], "total": 0},
        {"metadata": {}, "total": 0},
        {"metadata": {}, "products": []},
        {"metadata": {}, "products": {}, "total": 0},
        {"metadata": {}, "products": [], "total": None},
    ],
)
def test_response_contract_rejects_incomplete_or_invalid_payloads(payload):
    with pytest.raises(SystemExit) as caught:
        load_module().validate_response(payload)
    message = str(caught.value)
    assert "unexpected response schema" in message.lower()
    assert json.dumps(payload) not in message


def test_main_rejects_routing_only_response(monkeypatch):
    mod = load_module()
    monkeypatch.setattr(mod, "search", lambda *_args: {"metadata": {"shard": "x"}, "total": 0})
    monkeypatch.setattr(mod, "token_from", lambda _args: "token")
    monkeypatch.setattr(sys, "argv", ["wb_ge.py", "lego"])
    with pytest.raises(SystemExit) as caught:
        mod.main()
    assert "unexpected response schema" in str(caught.value).lower()


def test_request_shape_keeps_token_out_of_url():
    mod = load_module()
    payload = {"metadata": {}, "products": [], "total": 0}
    captured = {}

    def fake_open(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(payload)

    with patch.object(mod.urllib.request, "urlopen", side_effect=fake_open):
        got = mod.search("lego technic", 2, "priceup", "secret-value")

    assert got == payload
    request = captured["request"]
    assert "curr=gel" in request.full_url
    assert "locale=ge" in request.full_url
    assert "query=lego+technic" in request.full_url
    assert "page=2" in request.full_url
    assert "secret-value" not in request.full_url
    assert request.get_header("Cookie") == "x_wbaas_token=secret-value"
    assert captured["timeout"] == 25
