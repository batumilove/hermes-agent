"""Offline behavioral regression coverage for fleet mention routing."""
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.mattermost.adapter import MattermostAdapter


@pytest.fixture
def adapter():
    obj = MattermostAdapter(PlatformConfig(enabled=True, token="synthetic-token", extra={
        "url": "https://mattermost.invalid", "require_mention": True,
        "free_response_channels": [], "allowed_channels": [],
    }))
    obj._bot_username = "hermes"
    obj._bot_user_id = "self_id"
    obj.handle_message = AsyncMock()
    return obj


@pytest.mark.parametrize("text,expected", [
    ("@hermes hello", "hello"), ("(@HeRmEs) hello", "() hello"),
    ("@self_id hello", "hello"),
    ("@hermes-shopping hello", None), ("@hermes_extra hello", None),
    ("@hermes.other hello", None), ("@hermes:remote hello", None),
    ("person@hermes.example hello", None), ("person+tag@hermes hello", None),
    ("@hermes @hermes-shopping hello", "@hermes-shopping hello"),
    ("@hermes hello @hermes", "hello"),
])
def test_exact_identity_and_cleaning(adapter, text, expected):
    assert adapter._apply_channel_gating("channel", text) == expected


@pytest.mark.parametrize("mode", ["free", "unrestricted"])
@pytest.mark.parametrize("name", ["hermes-shopping", "hermes-research", "hermes-pa", "hermes-ops-watchdogs", "hermes-weingut-wolf"])
def test_other_fleet_target_overrides_free_response(adapter, mode, name):
    adapter.config.extra.update(require_mention=mode != "unrestricted", free_response_channels=["channel"] if mode == "free" else [])
    assert adapter._apply_channel_gating("channel", f"@{name} hello") is None


@pytest.mark.parametrize("text", ["hello", "@alice hello", "@hermes-unlisted hello", "person@hermes-shopping.example", "@hermes-shopping:remote hello"])
def test_nonfleet_names_do_not_silence_free_channel(adapter, text):
    adapter.config.extra["free_response_channels"] = ["channel"]
    assert adapter._apply_channel_gating("channel", text) == text


@pytest.mark.parametrize("text,free_channel", [
    ("@hermes+tag", False),
    ("@hermes%tag", False),
    ("@hermes-researché", False),
    ("@hermes:remoteé", False),
    ("@hermes-research+tag", True),
])
def test_nonmention_suffix_cannot_dispatch_strip_or_suppress(adapter, text, free_channel):
    if free_channel:
        adapter.config.extra["free_response_channels"] = ["channel"]
    assert adapter._apply_channel_gating("channel", text) == (text if free_channel else None)


def test_blank_identity_is_not_bare_at_mention(adapter):
    adapter._bot_username = adapter._bot_user_id = ""
    assert adapter._apply_channel_gating("channel", "hello @alice") is None


def test_allowlist_precedes_self_mention(adapter):
    adapter.config.extra["allowed_channels"] = ["elsewhere"]
    assert adapter._apply_channel_gating("channel", "@hermes hello") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,text,expected", [
    ("D", "@hermes-shopping hello", "@hermes-shopping hello"),
    ("D", "@hermes hello", "@hermes hello"),
    ("D", "hello", "hello"),
    ("O", "@hermes hello", "hello"),
    ("O", "@hermes-shopping hello", None),
    ("O", "@hermes @hermes-shopping hello", "@hermes-shopping hello"),
])
async def test_actual_websocket_dispatch(adapter, kind, text, expected):
    await adapter._handle_ws_event({"event": "posted", "data": {
        "channel_type": kind, "sender_name": "synthetic-user",
        "post": json.dumps({"id": "post", "user_id": "sender", "channel_id": "channel", "message": text}),
    }})
    if expected is None:
        adapter.handle_message.assert_not_awaited()
    else:
        adapter.handle_message.assert_awaited_once()
        assert adapter.handle_message.call_args.args[0].text == expected


def test_secondary_fleet_bot_rejects_primary_target(adapter):
    adapter._bot_username = "hermes-shopping"
    adapter.config.extra["free_response_channels"] = ["channel"]
    assert adapter._apply_channel_gating("channel", "@hermes hello") is None
    assert adapter._apply_channel_gating("channel", "@hermes-shopping hello") == "hello"
    assert adapter._apply_channel_gating("channel", "@hermes @hermes-shopping hello") == "@hermes  hello"


def test_import_is_candidate_not_live():
    import plugins.platforms.mattermost.adapter as module
    assert Path(module.__file__).resolve().is_relative_to(Path.cwd())
