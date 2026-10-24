"""Tests for the Buzz platform adapter plugin."""

import asyncio
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource, build_session_key
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

# Load plugins/platforms/buzz/adapter.py under a unique module name
# (plugin_adapter_buzz) so it cannot collide with other plugin adapters
# loaded by sibling tests in the same process.
_buzz_mod = load_plugin_adapter("buzz")

CachedMedia = _buzz_mod.CachedMedia
BuzzAdapter = _buzz_mod.BuzzAdapter
hex_to_npub = _buzz_mod.hex_to_npub
npub_to_hex = _buzz_mod.npub_to_hex
_normalize_user_ref = _buzz_mod._normalize_user_ref
_cli_error_message = _buzz_mod._cli_error_message
_MAX_CLI_MESSAGE_CHARS = _buzz_mod._MAX_CLI_MESSAGE_CHARS
_resolve_private_key = _buzz_mod._resolve_private_key
_resolve_auth_tag = _buzz_mod._resolve_auth_tag
_event_reply_parent_id = _buzz_mod._event_reply_parent_id
check_requirements = _buzz_mod.check_requirements
validate_config = _buzz_mod.validate_config
register = _buzz_mod.register
_env_enablement = _buzz_mod._env_enablement
_standalone_send = _buzz_mod._standalone_send
_parse_target_ref = getattr(_buzz_mod, "_parse_target_ref", None)

# Real key pair (Chip's public identity — public information, not a secret)
SELF_PUBKEY = "9fd5c7ba6d3ef224da78f541e0fcb9c50f72cc63edb19aae76ac6a0474dfa860"
SELF_NPUB = "npub1nl2u0wnd8mezfknc74q7pl9ec58h9nrrakce4tnk434qgaxl4psqe5twr6"
OTHER_PUBKEY = "a" * 64
AGENT_PUBKEY = "b" * 64
CHANNEL = "ccc2bc1a-7a82-5a8f-8c4e-57a070cbe7cd"
# Real DM conversation as materialized by a hosted relay: `dms list` returns
# [] for it (#68871) while `channels list` shows it as name "DM", empty
# description, indistinguishable from a channel except via message p-tags.
DM_CHANNEL = "6468cc16-a114-4f23-8b8c-02c1655cbf6b"

_ENV_VARS = (
    "BUZZ_RELAY_URL",
    "BUZZ_PRIVATE_KEY",
    "BUZZ_CHANNELS",
    "BUZZ_HOME_CHANNEL",
    "BUZZ_ALLOWED_USERS",
    "BUZZ_REACTION_ONLY_USERS",
    "BUZZ_ALLOW_ALL_USERS",
    "BUZZ_POLL_INTERVAL",
    "BUZZ_AUTH_TAG",
    "BUZZ_CLI_PATH",
    "BUZZ_CREDENTIALS_FILE",
    "BUZZ_AUTH_TAG",
)

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Keep tests hermetic: no ambient Buzz env vars or real credentials."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_buzz_mod, "_DEFAULT_CREDENTIALS_DIR", tmp_path / "no-creds")
    yield

def _event(event_id, pubkey=OTHER_PUBKEY, content="hello", created_at=1000, kind=9):
    return {
        "id": event_id,
        "pubkey": pubkey,
        "content": content,
        "created_at": created_at,
        "kind": kind,
        "tags": [["h", CHANNEL]],
    }

def _make_adapter(extra=None):
    from gateway.config import PlatformConfig

    cfg = PlatformConfig(enabled=True, extra={"relay_url": "https://test.relay", **(extra or {})})
    adapter = BuzzAdapter(cfg)
    adapter._self_pubkey = SELF_PUBKEY
    adapter._self_npub = SELF_NPUB
    adapter._display_name = "Chip"
    adapter._private_key = "nsec1test"
    # GatewayRunner installs this callback before intake starts. Attachment
    # tests are authorized by default and override the callback at the boundary.
    adapter.set_authorization_check(lambda *_args: True)
    return adapter

class _ScriptedCli:
    """Fake ``_run_cli`` that routes on the buzz subcommand and records calls."""

    def __init__(self):
        self.responses = {}  # (group, cmd) -> list of (code, stdout, stderr)
        self.calls = []

    def script(self, group, cmd, payload, code=0, stderr=""):
        stdout = payload if isinstance(payload, str) else json.dumps(payload)
        self.responses.setdefault((group, cmd), []).append((code, stdout, stderr))

    async def __call__(self, args, *, input_text=None):
        self.calls.append((list(args), input_text))
        queue = self.responses.get((args[0], args[1]), [])
        if len(queue) > 1:
            return queue.pop(0)
        if queue:
            return queue[0]
        return 0, "[]", ""


async def _hold_typing(_chat_id, interval=2.0, metadata=None, stop_event=None):
    if stop_event is not None:
        await stop_event.wait()
    else:
        await asyncio.Event().wait()


def _message_event():
    return MessageEvent(
        text="send the artifact",
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform("buzz"), chat_id=CHANNEL, chat_type="dm"),
        message_id="incoming-1",
    )


def _safe_media_file(tmp_path, monkeypatch, name, payload=b"payload"):
    root = tmp_path / "media-cache"
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    monkeypatch.setattr("gateway.platforms.base.MEDIA_DELIVERY_SAFE_ROOTS", (root,))
    return path.resolve()


# ── bech32 / identity helpers ─────────────────────────────────────────────

class TestBech32Helpers:

    def test_hex_to_npub_known_pair(self):
        assert hex_to_npub(SELF_PUBKEY) == SELF_NPUB

    def test_npub_to_hex_known_pair(self):
        assert npub_to_hex(SELF_NPUB) == SELF_PUBKEY

# ── Adapter init / config precedence ──────────────────────────────────────

class TestBuzzAdapterInit:

    def test_env_overrides_config(self, monkeypatch):
        monkeypatch.setenv("BUZZ_RELAY_URL", "https://env.relay")
        from gateway.config import PlatformConfig
        adapter = BuzzAdapter(PlatformConfig(enabled=True, extra={"relay_url": "https://cfg.relay"}))
        assert adapter.relay_url == "https://env.relay"

    @pytest.mark.asyncio
    async def test_list_channels_exposes_discovered_channels(self):
        adapter = _make_adapter()
        adapter._channel_names = {
            CHANNEL: "general",
            DM_CHANNEL: "DM",
        }
        adapter._channel_state = {
            CHANNEL: {"chat_type": "group", "last_ts": 0, "seen": {}},
            DM_CHANNEL: {"chat_type": "dm", "last_ts": 0, "seen": {}},
        }

        assert await adapter.list_channels() == [
            {"id": CHANNEL, "name": "general", "type": "group"},
            {"id": DM_CHANNEL, "name": "DM", "type": "dm"},
        ]


# ── CLI error contract ────────────────────────────────────────────────────

class TestCliErrorContract:

    def test_parses_json_error(self):
        msg = _cli_error_message('{"error":"relay_error","message":"boom","retryable":false}', 2)
        assert "relay_error" in msg and "boom" in msg and "exit 2" in msg

    @pytest.mark.parametrize(
        "stderr",
        [
            "x" * 100_000,
            json.dumps({"error": "relay_error", "message": "x" * 100_000}),
        ], ids=["plain-large", "json-large"],
    )
    def test_bounds_untrusted_cli_error_output(self, stderr):
        msg = _cli_error_message(stderr, 2)
        assert len(msg) <= 900
        assert msg.endswith("...")

# ── Seeding / high-water mark / de-dupe ───────────────────────────────────

class TestPollingDedupe:

    @pytest.fixture
    def adapter(self):
        a = _make_adapter()
        a._dispatched = []

        async def capture(**kwargs):
            a._dispatched.append(kwargs)

        a._dispatch_message = capture
        a._message_handler = AsyncMock()
        return a

    @pytest.mark.asyncio
    async def test_seed_sets_high_water_mark_without_dispatch(self, adapter):
        cli = _ScriptedCli()
        cli.script("messages", "get", [
            _event("e1", content="@Chip old history", created_at=100),
            _event("e2", content="@Chip newer history", created_at=200),
        ])
        adapter._run_cli = cli
        await adapter._seed_channel(CHANNEL, chat_type="group")

        state = adapter._channel_state[CHANNEL]
        assert state["last_ts"] == 200
        assert set(state["seen"]) == {"e1", "e2"}
        # Seeding must never replay history into the agent
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_new_event_dispatched_once(self, adapter):
        cli = _ScriptedCli()
        cli.script("messages", "get", [_event("e1", content="@Chip hi", created_at=100)])
        adapter._run_cli = cli
        await adapter._seed_channel(CHANNEL, chat_type="group")

        # Poll 1: seeded event + a genuinely new mention
        cli.responses.clear()
        cli.script("messages", "get", [
            _event("e1", content="@Chip hi", created_at=100),
            _event("e2", content="hey @Chip, ping", created_at=150),
        ])
        await adapter._poll_channel(CHANNEL)
        assert [d["message_id"] for d in adapter._dispatched] == ["e2"]
        assert adapter._dispatched[0]["text"] == "hey @Chip, ping"
        assert adapter._channel_state[CHANNEL]["last_ts"] == 150

        # Poll 2: identical response — the seen-id set must de-dupe
        await adapter._poll_channel(CHANNEL)
        assert len(adapter._dispatched) == 1

    @pytest.mark.asyncio
    async def test_malformed_attachment_url_does_not_abort_following_event(self, adapter):
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        malformed = _event("malformed-attachment", content="background chatter", created_at=159)
        malformed["tags"].append([
            "imeta",
            "url https://[invalid/media.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 1",
            "filename invalid.bin",
        ])
        valid = _event("following-valid", content="@Chip still there?", created_at=160)
        cli = _ScriptedCli()
        cli.script("messages", "get", [malformed, valid])
        adapter._run_cli = cli

        await adapter._poll_channel(CHANNEL)

        assert [item["message_id"] for item in adapter._dispatched] == ["following-valid"]

class TestInboundAttachments:

    def test_imeta_total_declared_bytes_are_bounded(self):
        event = _event("bounded", content="@Chip files")
        per_file_size = 6 * 1024 * 1024
        for index in range(4):
            event["tags"].append([
                "imeta",
                f"url https://test.relay/media/{index}.bin",
                "m application/octet-stream",
                "x " + format(index + 1, "064x"),
                f"size {per_file_size}",
                f"filename {index}.bin",
            ])

        attachments = BuzzAdapter._imeta_attachments(event)

        assert len(attachments) == 3
        assert sum(item["size"] for item in attachments) <= 20 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_download_caches_only_exact_size_and_sha256(self, monkeypatch):
        import httpx

        payload = b"verified Buzz attachment"
        digest = hashlib.sha256(payload).hexdigest()
        real_async_client = httpx.AsyncClient

        def handler(request):
            assert request.url.host == "test.relay"
            return httpx.Response(
                200,
                content=payload,
                headers={"content-length": str(len(payload))},
            )

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_async_client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )
        adapter = _make_adapter()

        cached = await adapter._download_attachment({
            "url": "https://test.relay/media/verified.bin",
            "sha256": digest,
            "size": len(payload),
            "filename": "verified.txt",
            "mime_type": "text/plain",
        })

        assert cached is not None
        assert cached.kind == "document"
        assert cached.media_type == "text/plain"
        assert Path(cached.path).read_bytes() == payload

    @pytest.mark.asyncio
    async def test_download_rejects_untrusted_attachment_host_before_network(self, monkeypatch):
        import httpx

        def must_not_create_client(**kwargs):
            raise AssertionError("network client must not be created")

        monkeypatch.setattr(httpx, "AsyncClient", must_not_create_client)
        adapter = _make_adapter()

        cached = await adapter._download_attachment({
            "url": "https://untrusted.example/media/file.bin",
            "sha256": "a" * 64,
            "size": 12,
            "filename": "file.bin",
            "mime_type": "application/octet-stream",
        })

        assert cached is None

    @pytest.mark.asyncio
    async def test_download_rejects_unconfigured_nondefault_port_before_network(self, monkeypatch):
        import httpx

        def must_not_create_client(**kwargs):
            raise AssertionError("network client must not be created")

        monkeypatch.setattr(httpx, "AsyncClient", must_not_create_client)
        adapter = _make_adapter()

        cached = await adapter._download_attachment({
            "url": "https://test.relay:8443/media/file.bin",
            "sha256": "a" * 64,
            "size": 12,
            "filename": "file.bin",
            "mime_type": "application/octet-stream",
        })

        assert cached is None

    @pytest.mark.asyncio
    async def test_download_allows_explicitly_configured_nondefault_port(self, monkeypatch):
        import httpx

        payload = b"x"
        real_async_client = httpx.AsyncClient

        def handler(request):
            assert request.url.port == 8443
            return httpx.Response(200, content=payload, headers={"content-length": "1"})

        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_async_client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            ),
        )
        adapter = _make_adapter({"attachment_hosts": ["test.relay:8443"]})

        cached = await adapter._download_attachment({
            "url": "https://test.relay:8443/media/file.bin",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": 1,
            "filename": "file.bin",
            "mime_type": "application/octet-stream",
        })

        assert cached is not None

    @pytest.mark.asyncio
    async def test_unaddressed_channel_attachment_is_not_downloaded(self):
        adapter = _make_adapter()
        authorization_check = MagicMock(return_value=True)
        adapter.set_authorization_check(authorization_check)
        adapter._cache_inbound_attachments = AsyncMock()
        adapter._download_attachment = AsyncMock()
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        event = _event("unaddressed-attachment", content="shared file")
        event["tags"].append([
            "imeta",
            "url https://test.relay/media/file.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 12",
            "filename file.bin",
        ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        authorization_check.assert_not_called()
        adapter._cache_inbound_attachments.assert_not_awaited()
        adapter._download_attachment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_attachment_download_is_visible_to_agent(self):
        adapter = _make_adapter()
        adapter._download_attachment = AsyncMock(return_value=None)
        adapter._user_names[OTHER_PUBKEY] = "Other"
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        dispatched = []

        async def capture(**kwargs):
            dispatched.append(kwargs)

        adapter._dispatch_message = capture
        event = _event("failed-attachment", content="@Chip inspect this")
        event["tags"].append([
            "imeta",
            "url https://test.relay/media/file.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 12",
            "filename file.bin",
        ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        assert "could not be downloaded" in dispatched[-1]["text"]
        assert dispatched[-1]["media_urls"] == []

    def test_imeta_sanitizes_filename_and_rejects_incomplete_metadata(self):
        event = _event("metadata", content="@Chip files")
        event["tags"].extend([
            [
                "imeta",
                "url https://test.relay/media/valid.bin",
                "m text/plain",
                "x " + "a" * 64,
                "size 12",
                "filename ../../private/report.txt",
            ],
            [
                "imeta",
                "url http://test.relay/media/insecure.bin",
                "x " + "b" * 64,
                "size 12",
                "filename insecure.bin",
            ],
            [
                "imeta",
                "url https://test.relay/media/no-hash.bin",
                "size 12",
                "filename no-hash.bin",
            ],
        ])

        attachments = BuzzAdapter._imeta_attachments(event)

        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.txt"

    @pytest.mark.asyncio
    async def test_attachment_only_dm_is_downloaded_and_dispatched(self):
        adapter = _make_adapter()
        attachment = CachedMedia(
            path="/agent/cache/doc_attachment.bin",
            media_type="application/octet-stream",
            kind="document",
            display_name="attachment.bin",
        )
        adapter._download_attachment = AsyncMock(return_value=attachment)
        adapter._user_names[OTHER_PUBKEY] = "Other"
        adapter._channel_state[DM_CHANNEL] = {
            "chat_type": "dm",
            "last_ts": 0,
            "seen": {},
        }
        dispatched = []

        async def capture(**kwargs):
            dispatched.append(kwargs)

        adapter._dispatch_message = capture
        event = _event("attachment-only", content="")
        event["tags"].append([
            "imeta",
            "url https://test.relay/media/file.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 12",
            "filename attachment.bin",
        ])

        await adapter._handle_event(
            DM_CHANNEL,
            adapter._channel_state[DM_CHANNEL],
            event,
        )

        adapter._download_attachment.assert_awaited_once()
        assert dispatched[-1]["media_urls"] == [attachment.path]
        assert dispatched[-1]["message_type"] is MessageType.DOCUMENT

    @pytest.mark.asyncio
    async def test_malformed_attachment_only_dm_dispatches_once_with_bounded_note(self):
        adapter = _make_adapter()
        adapter._download_attachment = AsyncMock()
        adapter._user_names[OTHER_PUBKEY] = "Other"
        adapter._channel_state[DM_CHANNEL] = {
            "chat_type": "dm",
            "last_ts": 0,
            "seen": {},
        }
        adapter._dispatch_message = AsyncMock()
        event = _event("malformed-attachment-only", content="")
        event["tags"].append(["imeta", "url definitely-not-a-url"])

        await adapter._handle_event(DM_CHANNEL, adapter._channel_state[DM_CHANNEL], event)
        await adapter._handle_event(DM_CHANNEL, adapter._channel_state[DM_CHANNEL], event)

        adapter._download_attachment.assert_not_awaited()
        adapter._dispatch_message.assert_awaited_once()
        call = adapter._dispatch_message.await_args
        assert call is not None
        dispatched = call.kwargs
        assert "1 Buzz attachment(s) rejected" in dispatched["text"]
        assert len(dispatched["text"]) <= 80
        assert dispatched["media_urls"] == []

    @pytest.mark.asyncio
    async def test_excess_imeta_is_reported_while_accepted_files_remain_attached(self):
        adapter = _make_adapter()
        adapter._user_names[OTHER_PUBKEY] = "Other"
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        adapter._download_attachment = AsyncMock(
            side_effect=lambda metadata: CachedMedia(
                f"/cache/{metadata['filename']}",
                metadata["mime_type"],
                "document",
                metadata["filename"],
            )
        )
        adapter._dispatch_message = AsyncMock()
        event = _event("excess-imeta", content="@Chip inspect")
        for index in range(5):
            event["tags"].append([
                "imeta",
                f"url https://test.relay/media/{index}.bin",
                "m application/octet-stream",
                "x " + format(index + 1, "064x"),
                "size 1",
                f"filename {index}.bin",
            ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        assert adapter._download_attachment.await_count == 4
        call = adapter._dispatch_message.await_args
        assert call is not None
        dispatched = call.kwargs
        assert "1 Buzz attachment(s) rejected" in dispatched["text"]
        assert len(dispatched["media_urls"]) == 4

    def test_imeta_bounds_filename_to_filesystem_safe_utf8_length(self):
        event = _event("long-name", content="@Chip file")
        event["tags"].append([
            "imeta",
            "url https://test.relay/media/file.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 1",
            "filename " + ("é" * 180) + ".pdf",
        ])

        filename = BuzzAdapter._imeta_attachments(event)[0]["filename"]

        assert len(filename.encode("utf-8")) <= 120
        assert filename.endswith(".pdf")

    @pytest.mark.asyncio
    async def test_cache_write_failure_is_treated_as_failed_attachment(self, monkeypatch):
        import httpx

        payload = b"x"
        digest = hashlib.sha256(payload).hexdigest()
        real_async_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_async_client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        content=payload,
                        headers={"content-length": "1"},
                    )
                ),
                **kwargs,
            ),
        )
        monkeypatch.setattr(
            _buzz_mod,
            "cache_media_bytes_async",
            AsyncMock(side_effect=OSError(36, "File name too long")),
        )
        adapter = _make_adapter()

        cached = await adapter._download_attachment({
            "url": "https://test.relay/media/file.bin",
            "sha256": digest,
            "size": 1,
            "filename": "file.bin",
            "mime_type": "application/octet-stream",
        })

        assert cached is None

    @pytest.mark.asyncio
    async def test_download_has_total_deadline(self, monkeypatch):
        import httpx

        payload = b"x"
        real_async_client = httpx.AsyncClient

        async def slow_handler(_request):
            await asyncio.sleep(0.05)
            return httpx.Response(200, content=payload, headers={"content-length": "1"})

        monkeypatch.setattr(_buzz_mod, "_ATTACHMENT_DOWNLOAD_TIMEOUT", 0.01)
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_async_client(
                transport=httpx.MockTransport(slow_handler),
                **kwargs,
            ),
        )
        adapter = _make_adapter()

        cached = await adapter._download_attachment({
            "url": "https://test.relay/media/file.bin",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size": 1,
            "filename": "file.bin",
            "mime_type": "application/octet-stream",
        })

        assert cached is None

    @pytest.mark.asyncio
    async def test_multiple_mixed_attachments_use_document_semantics(self):
        adapter = _make_adapter()
        adapter._user_names[OTHER_PUBKEY] = "Other"
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        cached = [
            CachedMedia("/cache/image.png", "image/png", "image", "image.png"),
            CachedMedia("/cache/audio.mp3", "audio/mpeg", "audio", "audio.mp3"),
        ]
        adapter._cache_inbound_attachments = AsyncMock(return_value=cached)
        dispatched = []

        async def capture(**kwargs):
            dispatched.append(kwargs)

        adapter._dispatch_message = capture
        event = _event("mixed", content="@Chip inspect")
        for index, mime_type in enumerate(("image/png", "audio/mpeg")):
            event["tags"].append([
                "imeta",
                f"url https://test.relay/media/{index}.bin",
                f"m {mime_type}",
                "x " + format(index + 1, "064x"),
                "size 1",
                f"filename {index}.bin",
            ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        assert dispatched[-1]["message_type"] is MessageType.DOCUMENT
        assert dispatched[-1]["media_types"] == ["image/png", "audio/mpeg"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "payload", "headers", "declared_size", "expected_digest"),
        [
            (302, b"", {}, 1, "a" * 64),
            (200, b"x", {"content-length": "invalid"}, 1, hashlib.sha256(b"x").hexdigest()),
            (200, b"x", {}, 2, hashlib.sha256(b"x").hexdigest()),
            (200, b"xx", {}, 1, hashlib.sha256(b"xx").hexdigest()),
            (200, b"x", {}, 1, "a" * 64),
        ],
    )
    async def test_download_rejects_invalid_response_or_integrity(
        self,
        monkeypatch,
        status,
        payload,
        headers,
        declared_size,
        expected_digest,
    ):
        import httpx

        real_async_client = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real_async_client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(status, content=payload, headers=headers)
                ),
                **kwargs,
            ),
        )
        adapter = _make_adapter()

        cached = await adapter._download_attachment({
            "url": "https://test.relay/media/file.bin",
            "sha256": expected_digest,
            "size": declared_size,
            "filename": "file.bin",
            "mime_type": "application/octet-stream",
        })

        assert cached is None

    def test_imeta_rejects_url_credentials_and_fragments_and_caps_items(self):
        event = _event("url-safety", content="@Chip files")
        event["tags"].extend([
            [
                "imeta",
                "url https://user:password@test.relay/media/private.bin",
                "x " + "a" * 64,
                "size 1",
                "filename private.bin",
            ],
            [
                "imeta",
                "url https://test.relay/media/fragment.bin#hidden",
                "x " + "b" * 64,
                "size 1",
                "filename fragment.bin",
            ],
        ])
        for index in range(6):
            event["tags"].append([
                "imeta",
                f"url https://test.relay/media/{index}.bin",
                "x " + format(index + 1, "064x"),
                "size 1",
                f"filename {index}.bin",
            ])

        attachments = BuzzAdapter._imeta_attachments(event)

        assert len(attachments) == 4
        assert all("@" not in item["url"] and "#" not in item["url"] for item in attachments)

    @pytest.mark.asyncio
    async def test_self_attachment_stops_before_authorization_or_cache(self):
        adapter = _make_adapter()
        authorization_check = MagicMock(return_value=True)
        adapter.set_authorization_check(authorization_check)
        adapter._cache_inbound_attachments = AsyncMock()
        adapter._dispatch_message = AsyncMock()
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        event = _event("self-attachment", pubkey=SELF_PUBKEY, content="@Chip inspect")
        event["tags"].append([
            "imeta",
            "url https://test.relay/media/file.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 1",
            "filename file.bin",
        ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        authorization_check.assert_not_called()
        adapter._cache_inbound_attachments.assert_not_awaited()
        adapter._dispatch_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unauthorized_sender_attachment_is_not_downloaded(self):
        adapter = _make_adapter()
        adapter._allowed_pubkeys = {"f" * 64}
        authorization_check = MagicMock(return_value=True)
        adapter.set_authorization_check(authorization_check)
        adapter._cache_inbound_attachments = AsyncMock()
        adapter._download_attachment = AsyncMock()
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        event = _event("unauthorized-attachment", content="@Chip inspect")
        event["tags"].append([
            "imeta",
            "url https://test.relay/media/file.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 1",
            "filename file.bin",
        ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        authorization_check.assert_not_called()
        adapter._cache_inbound_attachments.assert_not_awaited()
        adapter._download_attachment.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("authorization", [False, None, "raise", "truthy"])
    async def test_non_true_gateway_authority_never_caches_even_for_locally_allowed_sender(
        self,
        authorization,
    ):
        adapter = _make_adapter()
        adapter._run_cli = AsyncMock(side_effect=AssertionError("authorization test invoked Buzz CLI"))
        adapter._resolve_user_name = AsyncMock(return_value="Other")
        adapter._allowed_pubkeys = {OTHER_PUBKEY}
        if authorization is None:
            adapter.set_authorization_check(None)
        elif authorization == "raise":
            def raise_unknown(*_args):
                raise RuntimeError("authorization backend unavailable")

            adapter.set_authorization_check(raise_unknown)
        elif authorization == "truthy":
            adapter.set_authorization_check(lambda *_args: "AUTHORIZED")
        else:
            adapter.set_authorization_check(lambda *_args: False)
        adapter._cache_inbound_attachments = AsyncMock()
        adapter._dispatch_message = AsyncMock()
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        event = _event(f"gateway-{authorization}-attachment", content="@Chip inspect")
        event["tags"].append([
            "imeta",
            "url https://test.relay/media/file.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 1",
            "filename file.bin",
        ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        adapter._cache_inbound_attachments.assert_not_awaited()
        adapter._dispatch_message.assert_awaited_once()
        call = adapter._dispatch_message.await_args
        assert call is not None
        assert call.kwargs["media_urls"] == []

    @pytest.mark.asyncio
    async def test_explicit_true_gateway_authority_caches_attachment(self):
        adapter = _make_adapter()
        adapter._run_cli = AsyncMock(side_effect=AssertionError("authorization test invoked Buzz CLI"))
        adapter._resolve_user_name = AsyncMock(return_value="Other")
        authorization_check = MagicMock(return_value=True)
        adapter.set_authorization_check(authorization_check)
        cached = CachedMedia(
            "/cache/authorized.bin",
            "application/octet-stream",
            "document",
            "authorized.bin",
        )
        adapter._cache_inbound_attachments = AsyncMock(return_value=[cached])
        adapter._dispatch_message = AsyncMock()
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        event = _event("gateway-authorized-attachment", content="@Chip inspect")
        event["tags"].append([
            "imeta",
            "url https://test.relay/media/file.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 1",
            "filename file.bin",
        ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        authorization_check.assert_called_once_with(OTHER_PUBKEY, "group", CHANNEL)
        adapter._cache_inbound_attachments.assert_awaited_once()
        call = adapter._dispatch_message.await_args
        assert call is not None
        assert call.kwargs["media_urls"] == [cached.path]

    @pytest.mark.asyncio
    async def test_real_gateway_auth_callback_defaults_to_no_attachment_side_effects(
        self,
        monkeypatch,
    ):
        from gateway.config import GatewayConfig
        from gateway.run import GatewayRunner

        monkeypatch.delenv("GATEWAY_ALLOWED_USERS", raising=False)
        monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
        runner = object.__new__(GatewayRunner)
        runner.config = GatewayConfig()
        runner.adapters = {}
        runner.pairing_store = MagicMock()
        runner.pairing_store.is_approved.return_value = False

        adapter = _make_adapter()
        adapter._run_cli = AsyncMock(side_effect=AssertionError("authorization test invoked Buzz CLI"))
        adapter._resolve_user_name = AsyncMock(return_value="Other")
        adapter._allowed_pubkeys = {OTHER_PUBKEY}
        adapter.set_authorization_check(runner._make_adapter_auth_check(adapter.platform))
        adapter._cache_inbound_attachments = AsyncMock()
        adapter._dispatch_message = AsyncMock()
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        event = _event("gateway-default-denied-attachment", content="@Chip inspect")
        event["tags"].append([
            "imeta",
            "url https://test.relay/media/file.bin",
            "m application/octet-stream",
            "x " + "a" * 64,
            "size 1",
            "filename file.bin",
        ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        runner.pairing_store.is_approved.assert_called_once_with("buzz", OTHER_PUBKEY)
        adapter._cache_inbound_attachments.assert_not_awaited()
        adapter._dispatch_message.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("kind", "mime_type", "expected_type"),
        [
            ("image", "image/png", MessageType.PHOTO),
            ("video", "video/mp4", MessageType.VIDEO),
            ("audio", "audio/mpeg", MessageType.AUDIO),
            ("document", "application/pdf", MessageType.DOCUMENT),
        ],
    )
    async def test_homogeneous_attachment_kind_sets_message_type(
        self,
        kind,
        mime_type,
        expected_type,
    ):
        adapter = _make_adapter()
        adapter._user_names[OTHER_PUBKEY] = "Other"
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        cached = CachedMedia(
            f"/cache/file-{kind}",
            mime_type,
            kind,
            f"file-{kind}",
        )
        adapter._cache_inbound_attachments = AsyncMock(return_value=[cached])
        dispatched = []

        async def capture(**kwargs):
            dispatched.append(kwargs)

        adapter._dispatch_message = capture
        event = _event(f"homogeneous-{kind}", content="@Chip inspect")
        event["tags"].append([
            "imeta",
            f"url https://test.relay/media/{kind}",
            f"m {mime_type}",
            "x " + "a" * 64,
            "size 1",
            f"filename file-{kind}",
        ])

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        assert dispatched[-1]["message_type"] is expected_type

# ── Mention gating / DMs / authorization ──────────────────────────────────

class TestMentionGating:

    @pytest.fixture
    def adapter(self):
        a = _make_adapter()
        a._dispatched = []

        async def capture(**kwargs):
            a._dispatched.append(kwargs)

        a._dispatch_message = capture
        a._message_handler = AsyncMock()
        a._channel_state[CHANNEL] = {"chat_type": "group", "last_ts": 0, "seen": {}}
        return a

    async def _poll_with(self, adapter, *events):
        cli = _ScriptedCli()
        cli.script("messages", "get", list(events))
        adapter._run_cli = cli
        await adapter._poll_channel(CHANNEL)

    @pytest.mark.asyncio
    async def test_unaddressed_channel_message_ignored(self, adapter):
        await self._poll_with(adapter, _event("e1", content="just chatting", created_at=10))
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_name_mention_dispatched(self, adapter):
        await self._poll_with(adapter, _event("e1", content="hey @Chip can you help?", created_at=10))
        assert len(adapter._dispatched) == 1
        assert adapter._dispatched[0]["text"] == "hey @Chip can you help?"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content",
        [
            "Chip should stay silent",
            "ask @Chipmunk instead",
            "ask @Chip-bot instead",
            "email chip@example.com",
        ],
    )
    async def test_bare_or_prefix_name_does_not_dispatch(self, adapter, content):
        await self._poll_with(adapter, _event("e1", content=content, created_at=10))
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("identity", [SELF_NPUB, SELF_PUBKEY])
    async def test_identity_text_dispatches(self, adapter, identity):
        await self._poll_with(
            adapter,
            _event("e1", content=f"please check {identity}", created_at=10),
        )
        assert len(adapter._dispatched) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content",
        [f"a{SELF_PUBKEY}b", f"x{SELF_NPUB}y"],
    )
    async def test_identity_substring_does_not_dispatch(self, adapter, content):
        await self._poll_with(
            adapter,
            _event("e1", content=content, created_at=10),
        )
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_signed_recipient_tag_dispatches_without_text_mention(self, adapter):
        event = _event("e1", content="please take a look", created_at=10)
        event["tags"].append(["p", SELF_PUBKEY])
        await self._poll_with(adapter, event)
        assert len(adapter._dispatched) == 1

    @pytest.mark.asyncio
    async def test_other_recipient_tag_does_not_dispatch(self, adapter):
        event = _event("e1", content="please take a look", created_at=10)
        event["tags"].append(["p", "b" * 64])
        await self._poll_with(adapter, event)
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_require_mention_false_still_dispatches_unaddressed_message(self, adapter):
        adapter.require_mention = False
        await self._poll_with(adapter, _event("e1", content="just chatting", created_at=10))
        assert len(adapter._dispatched) == 1

    def test_strip_mention_requires_at_for_display_name(self, adapter):
        assert adapter._strip_mention("@Chip: /whoami") == "/whoami"
        assert adapter._strip_mention("Chip: please review") == "Chip: please review"
        assert adapter._strip_mention("@Chip-bot: please review") == "@Chip-bot: please review"

    @pytest.mark.asyncio
    async def test_allowlist_blocks_unauthorized(self, adapter):
        adapter._allowed_pubkeys = {"b" * 64}
        await self._poll_with(adapter, _event("e1", content="@Chip hello", created_at=10))
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_explicit_agent_tag_reacts_without_dispatch(self, adapter):
        adapter._allowed_pubkeys = {OTHER_PUBKEY}
        adapter._reaction_only_pubkeys = {AGENT_PUBKEY}
        adapter.send_reaction = AsyncMock(return_value=True)
        event = _event("e1", pubkey=AGENT_PUBKEY, content="@Chip coordinate", created_at=10)
        event["tags"].append(["p", SELF_PUBKEY])

        await self._poll_with(adapter, event)

        adapter.send_reaction.assert_awaited_once_with(CHANNEL, "e1", "👀")
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_allowlist_takes_precedence_over_reaction_only(self, adapter):
        adapter._allowed_pubkeys = {AGENT_PUBKEY}
        adapter._reaction_only_pubkeys = {AGENT_PUBKEY}
        adapter.send_reaction = AsyncMock(return_value=True)
        event = _event("e1", pubkey=AGENT_PUBKEY, content="@Chip coordinate", created_at=10)
        event["tags"].append(["p", SELF_PUBKEY])

        await self._poll_with(adapter, event)

        adapter.send_reaction.assert_not_awaited()
        assert len(adapter._dispatched) == 1

    @pytest.mark.asyncio
    async def test_agent_message_without_explicit_recipient_gets_no_reaction(self, adapter):
        adapter._allowed_pubkeys = {OTHER_PUBKEY}
        adapter._reaction_only_pubkeys = {AGENT_PUBKEY}
        adapter.send_reaction = AsyncMock(return_value=True)

        await self._poll_with(
            adapter,
            _event("e1", pubkey=AGENT_PUBKEY, content="@Chip coordinate", created_at=10),
        )

        adapter.send_reaction.assert_not_awaited()
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_unknown_sender_tag_gets_no_reaction(self, adapter):
        adapter._allowed_pubkeys = {OTHER_PUBKEY}
        adapter._reaction_only_pubkeys = {AGENT_PUBKEY}
        adapter.send_reaction = AsyncMock(return_value=True)
        event = _event("e1", pubkey="c" * 64, content="@Chip coordinate", created_at=10)
        event["tags"].append(["p", SELF_PUBKEY])

        await self._poll_with(adapter, event)

        adapter.send_reaction.assert_not_awaited()
        assert adapter._dispatched == []

# ── NIP-10 thread replies as addressed (issue #75826) ────────────────────
#
# With require_mention (default), channel replies whose direct parent is the
# agent's own message must dispatch even when the text has no @name — Buzz
# Desktop's natural reply affordance for /approve never types a mention.

def _tagged_event(event_id, channel, *, content, pubkey=OTHER_PUBKEY,
                  created_at=1000, kind=9, p=None, reply_to=None, root=None):
    """Event with the tag shapes observed on a live relay (h/p/e tags)."""
    tags = [["h", channel]]
    # NIP-10 order as Desktop emits: root first, then reply (when both set).
    if root:
        tags.append(["e", root, "", "root"])
    if reply_to:
        tags.append(["e", reply_to, "", "reply"])
    if p:
        tags.append(["p", p])
    return {
        "id": event_id,
        "pubkey": pubkey,
        "content": content,
        "created_at": created_at,
        "kind": kind,
        "tags": tags,
    }

class TestNip10ThreadReplyMentionGate:
    """require_mention + NIP-10 reply-to-own-message (#75826)."""

    @pytest.fixture
    def adapter(self):
        a = _make_adapter()
        a._dispatched = []

        async def capture(**kwargs):
            a._dispatched.append(kwargs)

        a._dispatch_message = capture
        a._message_handler = AsyncMock()
        a._channel_state[CHANNEL] = a._new_channel_state("group")
        return a

    async def _poll_with(self, adapter, *events):
        cli = _ScriptedCli()
        cli.script("messages", "get", list(events))
        adapter._run_cli = cli
        await adapter._poll_channel(CHANNEL)

    def test_event_reply_parent_prefers_reply_marker(self):
        ev = _tagged_event(
            "child", CHANNEL, content="ok", root="root-id", reply_to="parent-id"
        )
        assert _event_reply_parent_id(ev) == "parent-id"
        assert _event_reply_parent_id(
            _tagged_event("c2", CHANNEL, content="ok", root="only-root")
        ) == "only-root"

    @pytest.mark.asyncio
    async def test_thread_reply_to_own_message_dispatches_without_mention(self, adapter):
        # Live agent prompt lands first (self-echo is cached, not dispatched).
        await self._poll_with(
            adapter,
            _tagged_event(
                "agent-prompt",
                CHANNEL,
                content="⚠️ Dangerous command requires approval",
                pubkey=SELF_PUBKEY,
                created_at=10,
            ),
            _tagged_event(
                "user-reply",
                CHANNEL,
                content="sure go ahead",
                root="agent-prompt",
                reply_to="agent-prompt",
                created_at=11,
            ),
        )
        assert [d["message_id"] for d in adapter._dispatched] == ["user-reply"]
        assert adapter._dispatched[0]["text"] == "sure go ahead"
        assert adapter._dispatched[0]["reply_to_message_id"] == "agent-prompt"
        assert adapter._dispatched[0]["reply_to_is_own_message"] is True
        assert "approval" in (adapter._dispatched[0]["reply_to_text"] or "")

    @pytest.mark.asyncio
    async def test_reply_to_other_user_stays_gated(self, adapter):
        third = "c" * 64
        await self._poll_with(
            adapter,
            _tagged_event(
                "other-msg",
                CHANNEL,
                content="anyone around?",
                pubkey=third,
                created_at=30,
            ),
            _tagged_event(
                "reply-other",
                CHANNEL,
                content="yeah I'm here",
                root="other-msg",
                reply_to="other-msg",
                created_at=31,
            ),
        )
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_reply_to_unknown_parent_stays_gated(self, adapter):
        await self._poll_with(
            adapter,
            _tagged_event(
                "orphan-reply",
                CHANNEL,
                content="/approve session",
                root="never-seen",
                reply_to="never-seen",
                created_at=40,
            ),
        )
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_seeded_own_history_matches_thread_reply(self, adapter):
        """Replies to agent messages sent before a gateway restart still match."""
        cli = _ScriptedCli()
        cli.script(
            "messages",
            "get",
            [
                _tagged_event(
                    "pre-restart-agent",
                    CHANNEL,
                    content="⚠️ Dangerous command requires approval",
                    pubkey=SELF_PUBKEY,
                    created_at=50,
                ),
            ],
        )
        adapter._run_cli = cli
        await adapter._seed_channel(CHANNEL, chat_type="group")
        assert "pre-restart-agent" in adapter._channel_state[CHANNEL]["event_meta"]
        assert adapter._dispatched == []

        cli.responses.clear()
        cli.script(
            "messages",
            "get",
            [
                _tagged_event(
                    "post-restart-approve",
                    CHANNEL,
                    content="/approve always",
                    root="pre-restart-agent",
                    reply_to="pre-restart-agent",
                    created_at=51,
                ),
            ],
        )
        await adapter._poll_channel(CHANNEL)
        assert [d["message_id"] for d in adapter._dispatched] == ["post-restart-approve"]
        assert adapter._dispatched[0]["reply_to_is_own_message"] is True

    @pytest.mark.asyncio
    async def test_send_recorded_id_matches_thread_reply(self, adapter):
        """send()'s returned event_id is cached even without a WS/poll echo."""
        cli = _ScriptedCli()
        cli.script(
            "messages",
            "send",
            {"accepted": True, "event_id": "sent-prompt", "message": ""},
        )
        adapter._run_cli = cli
        result = await adapter.send(
            CHANNEL, "⚠️ Dangerous command requires approval"
        )
        assert result.success is True
        assert "sent-prompt" in adapter._channel_state[CHANNEL]["event_meta"]
        meta = adapter._channel_state[CHANNEL]["event_meta"]["sent-prompt"]
        assert meta[0] == SELF_PUBKEY

        cli.responses.clear()
        cli.script(
            "messages",
            "get",
            [
                _tagged_event(
                    "reply-to-send",
                    CHANNEL,
                    content="/approve session",
                    root="sent-prompt",
                    reply_to="sent-prompt",
                    created_at=61,
                ),
            ],
        )
        await adapter._poll_channel(CHANNEL)
        assert [d["message_id"] for d in adapter._dispatched] == ["reply-to-send"]
        assert adapter._dispatched[0]["reply_to_is_own_message"] is True
        assert adapter._dispatched[0]["reply_to_message_id"] == "sent-prompt"

    @pytest.mark.asyncio
    async def test_mention_path_still_populates_reply_context(self, adapter):
        """Visible @mention + thread reply still fills reply_to_* on dispatch."""
        await self._poll_with(
            adapter,
            _tagged_event(
                "agent-prior",
                CHANNEL,
                content="previous answer",
                pubkey=SELF_PUBKEY,
                created_at=70,
            ),
            _tagged_event(
                "mentioned-reply",
                CHANNEL,
                content="@Chip follow up please",
                root="agent-prior",
                reply_to="agent-prior",
                created_at=71,
            ),
        )
        assert len(adapter._dispatched) == 1
        d = adapter._dispatched[0]
        assert d["message_id"] == "mentioned-reply"
        assert d["text"] == "follow up please"  # leading @Chip stripped
        assert d["reply_to_message_id"] == "agent-prior"
        assert d["reply_to_author_id"] == SELF_PUBKEY
        assert d["reply_to_is_own_message"] is True
        assert d["reply_to_text"] == "previous answer"

# ── DM classification via p-tags (issue #68871) ──────────────────────────
#
# `buzz dms list` returns [] on some hosted relays, so DM conversations leak
# in via `channels list` and get seeded chat_type="group".  The adapter must
# reclassify them from the Nostr tags of real traffic: DM messages are
# p-tagged to our own pubkey WITHOUT the text mentioning us, while channel
# messages only ever p-tag us when the text visibly @mentions us.

class TestDmClassification:

    @pytest.fixture
    def adapter(self):
        a = _make_adapter()
        a._dispatched = []

        async def capture(**kwargs):
            a._dispatched.append(kwargs)

        a._dispatch_message = capture
        a._message_handler = AsyncMock()
        # Metadata exactly as `channels list` returns it on the hosted relay.
        a._channel_meta = {
            DM_CHANNEL: {"channel_id": DM_CHANNEL, "name": "DM", "description": ""},
            CHANNEL: {
                "channel_id": CHANNEL,
                "name": "general",
                "description": "General conversation and community updates.",
            },
        }
        a._channel_names = {DM_CHANNEL: "DM", CHANNEL: "general"}
        # Both leaked in as group — the bug under test.
        a._channel_state[DM_CHANNEL] = {"chat_type": "group", "last_ts": 0, "seen": {}}
        a._channel_state[CHANNEL] = {"chat_type": "group", "last_ts": 0, "seen": {}}
        return a

    async def _poll_with(self, adapter, channel, *events):
        cli = _ScriptedCli()
        cli.script("messages", "get", list(events))
        adapter._run_cli = cli
        await adapter._poll_channel(channel)

    @pytest.mark.asyncio
    async def test_unmentioned_ptagged_dm_latches_and_dispatches(self, adapter):
        """The reported bug: a DM without an @mention must dispatch."""
        await self._poll_with(
            adapter, DM_CHANNEL,
            _tagged_event("e1", DM_CHANNEL, content="here's a test message", p=SELF_PUBKEY),
        )
        assert adapter._channel_state[DM_CHANNEL]["chat_type"] == "dm"
        assert [d["message_id"] for d in adapter._dispatched] == ["e1"]
        assert adapter._dispatched[0]["chat_type"] == "dm"

    @pytest.mark.asyncio
    async def test_allowlisted_sender_in_dm_metadata_dispatches_without_ptag(self, adapter):
        """Private-mode DMs remain usable when a relay omits recipient p-tags."""
        adapter._allowed_pubkeys = {OTHER_PUBKEY}
        await self._poll_with(
            adapter,
            DM_CHANNEL,
            _tagged_event("e1", DM_CHANNEL, content="hi"),
        )
        assert adapter._channel_state[DM_CHANNEL]["chat_type"] == "dm"
        assert [d["message_id"] for d in adapter._dispatched] == ["e1"]
        assert adapter._dispatched[0]["chat_type"] == "dm"

    @pytest.mark.asyncio
    async def test_unlisted_sender_without_ptag_stays_mention_gated(self, adapter):
        """DM-shaped metadata alone must not bypass private access control."""
        adapter._allowed_pubkeys = {"b" * 64}
        await self._poll_with(
            adapter,
            DM_CHANNEL,
            _tagged_event("e1", DM_CHANNEL, content="hi"),
        )
        assert adapter._channel_state[DM_CHANNEL]["chat_type"] == "group"
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_allow_all_mode_without_ptag_stays_mention_gated(self, adapter):
        """P-tag-less metadata fallback is restricted to private allow-list mode."""
        adapter._allowed_pubkeys = set()
        await self._poll_with(
            adapter,
            DM_CHANNEL,
            _tagged_event("e1", DM_CHANNEL, content="hi"),
        )
        assert adapter._channel_state[DM_CHANNEL]["chat_type"] == "group"
        assert adapter._dispatched == []

    @pytest.mark.asyncio
    async def test_allowlisted_sender_in_real_channel_stays_mention_gated(self, adapter):
        """An allow-listed sender cannot turn channel metadata into a DM."""
        adapter._allowed_pubkeys = {OTHER_PUBKEY}
        await self._poll_with(
            adapter,
            CHANNEL,
            _tagged_event("e1", CHANNEL, content="hi"),
        )
        assert adapter._channel_state[CHANNEL]["chat_type"] == "group"
        assert adapter._dispatched == []


    @pytest.mark.asyncio
    async def test_general_reply_ptagging_self_stays_channel(self, adapter):
        """A #general reply to us p-tags our pubkey (observed live) — that
        must NOT reclassify the channel; mention gating still applies."""
        await self._poll_with(
            adapter, CHANNEL,
            _tagged_event("e1", CHANNEL, content="@chip what's up?",
                          p=SELF_PUBKEY, reply_to="root-event"),
        )
        assert adapter._channel_state[CHANNEL]["chat_type"] == "group"
        # It carried a mention, so it dispatches — but as a group message.
        assert [d["chat_type"] for d in adapter._dispatched] == ["group"]

        # And once the mention is absent, the channel gate drops the message
        # even though the earlier reply p-tagged us.
        await self._poll_with(
            adapter, CHANNEL,
            _tagged_event("e2", CHANNEL, content="thanks everyone", created_at=1001),
        )
        assert len(adapter._dispatched) == 1

    @pytest.mark.asyncio
    async def test_channel_ptag_dispatches_without_latching(self, adapter):
        """A signed recipient tag wakes a channel without turning it into a DM."""
        adapter._channel_meta[CHANNEL]["description"] = ""
        adapter._channel_meta[CHANNEL]["name"] = "announcements"
        await self._poll_with(
            adapter, CHANNEL,
            _tagged_event("e1", CHANNEL, content="fyi everyone", p=SELF_PUBKEY),
        )
        assert adapter._channel_state[CHANNEL]["chat_type"] == "group"
        assert [d["message_id"] for d in adapter._dispatched] == ["e1"]

        await self._poll_with(
            adapter, CHANNEL,
            _tagged_event(
                "e2", CHANNEL, content="plain follow-up", created_at=1001, p=None
            ),
        )
        assert [d["message_id"] for d in adapter._dispatched] == ["e1"]

    @pytest.mark.asyncio
    async def test_missing_metadata_never_latches_group_as_dm(self, adapter):
        adapter._channel_meta.pop(CHANNEL)
        await self._poll_with(
            adapter, CHANNEL,
            _tagged_event("e1", CHANNEL, content="tag-only mention", p=SELF_PUBKEY),
        )
        assert adapter._channel_state[CHANNEL]["chat_type"] == "group"
        assert [d["message_id"] for d in adapter._dispatched] == ["e1"]
        assert adapter._may_reclassify_as_dm(CHANNEL) is False

    @pytest.mark.asyncio
    async def test_dm_shaped_channel_discovered_when_dms_list_empty(self):
        """Fallback discovery: with `dms list` broken (returns []), a
        DM-shaped `channels list` entry gets watched. In watch-all mode a
        real channel is adopted too (live join, #75107) — but seeded from
        history, so nothing is replayed."""
        a = _make_adapter()
        cli = _ScriptedCli()
        cli.script("dms", "list", [])
        cli.script("channels", "list", [
            {"channel_id": DM_CHANNEL, "name": "DM", "description": "", "created_at": 1},
            {"channel_id": CHANNEL, "name": "general",
             "description": "General conversation and community updates.", "created_at": 2},
        ])
        a._run_cli = cli
        await a._discover_dms(seed=False)
        assert a._channel_state[DM_CHANNEL]["chat_type"] == "dm"
        assert a._may_reclassify_as_dm(DM_CHANNEL) is True
        # Watch-all mode: the real channel is live-adopted (seeded, never
        # reclassified as DM).
        assert CHANNEL in a._channel_state
        assert a._channel_state[CHANNEL]["chat_type"] == "group"
        assert a._may_reclassify_as_dm(CHANNEL) is False
        # Adoption seeded it via a messages get call (history suppressed).
        assert any(c[0][:2] == ["messages", "get"] for c in cli.calls)

    @pytest.mark.asyncio
    async def test_explicit_watch_list_blocks_live_channel_adoption(self):
        """With an explicit channels: list, discovery must NOT adopt real
        channels outside that list — the user chose the watch set (#75107
        scoping)."""
        a = _make_adapter(extra={"channels": ["some-other-channel"]})
        cli = _ScriptedCli()
        cli.script("dms", "list", [])
        cli.script("channels", "list", [
            {"channel_id": CHANNEL, "name": "general",
             "description": "General conversation and community updates.", "created_at": 2},
        ])
        a._run_cli = cli
        await a._discover_dms(seed=False)
        assert CHANNEL not in a._channel_state

    @pytest.mark.asyncio
    async def test_dm_metadata_promotes_existing_group_without_recipient_tag(self, adapter):
        cli = _ScriptedCli()
        cli.script("dms", "list", [])
        cli.script("channels", "list", [adapter._channel_meta[DM_CHANNEL]])
        adapter._run_cli = cli
        await adapter._discover_dms(seed=False)
        assert adapter._channel_state[DM_CHANNEL]["chat_type"] == "dm"

        await self._poll_with(
            adapter, DM_CHANNEL,
            _tagged_event("e1", DM_CHANNEL, content="no mention and no p tag"),
        )
        assert [d["message_id"] for d in adapter._dispatched] == ["e1"]
        assert adapter._dispatched[0]["chat_type"] == "dm"

class TestThreadRoots:

    @pytest.mark.asyncio
    async def test_inbound_root_e_tag_propagates_to_session_source(self):
        adapter = _make_adapter()
        adapter._channel_state[CHANNEL] = {
            "chat_type": "group",
            "last_ts": 0,
            "seen": {},
        }
        dispatched = []

        async def capture(event):
            dispatched.append(event)

        adapter._message_handler = AsyncMock()
        adapter.handle_message = capture
        adapter._run_cli = _ScriptedCli()
        adapter.send_reaction = AsyncMock(return_value=True)
        event = _tagged_event("latest-child", CHANNEL, content="@Chip follow-up")
        event["tags"] += [
            ["e", "stable-root", "", "root"],
            ["e", "latest-parent", "", "reply"],
        ]

        await adapter._handle_event(CHANNEL, adapter._channel_state[CHANNEL], event)

        assert dispatched[0].source.thread_id == "stable-root"

# ── Sending ───────────────────────────────────────────────────────────────

class TestBuzzAdapterSend:

    @pytest.mark.asyncio
    async def test_send_success_via_stdin(self):
        adapter = _make_adapter()
        adapter._channel_state[CHANNEL] = {"chat_type": "group", "last_ts": 0, "seen": {}}
        cli = _ScriptedCli()
        cli.script("messages", "send", {"accepted": True, "event_id": "evt123", "message": ""})
        adapter._run_cli = cli

        result = await adapter.send(CHANNEL, "hello **markdown**")
        assert result.success is True
        assert result.message_id == "evt123"

        args, stdin_text = cli.calls[0]
        assert args[:2] == ["messages", "send"]
        assert args[args.index("--channel") + 1] == CHANNEL
        # Content travels via stdin (--content -), never argv
        assert args[args.index("--content") + 1] == "-"
        assert stdin_text == "hello **markdown**"
        # Our own event id is marked seen for echo suppression
        assert "evt123" in adapter._channel_state[CHANNEL]["seen"]

    @pytest.mark.asyncio
    async def test_send_uses_metadata_reply_to_message_id(self):
        """Gateway stream/progress pass reply anchors via metadata.

        Without honoring reply_to_message_id, mid-turn commentary posts as
        new top-level channel messages instead of thread replies.
        """
        adapter = _make_adapter()
        adapter._channel_state[CHANNEL] = {"chat_type": "group", "last_ts": 0, "seen": {}}
        cli = _ScriptedCli()
        cli.script("messages", "send", {"accepted": True, "event_id": "evt-reply", "message": ""})
        adapter._run_cli = cli

        result = await adapter.send(
            CHANNEL,
            "threaded reply",
            metadata={"reply_to_message_id": "root-event-abc"},
        )
        assert result.success is True
        args, _stdin = cli.calls[0]
        assert "--reply-to" in args
        assert args[args.index("--reply-to") + 1] == "root-event-abc"

    @pytest.mark.asyncio
    async def test_send_prefers_stable_thread_root_over_latest_reply(self):
        adapter = _make_adapter()
        cli = _ScriptedCli()
        cli.script("messages", "send", {"accepted": True, "event_id": "evt124"})
        adapter._run_cli = cli

        await adapter.send(
            CHANNEL,
            "threaded reply",
            reply_to="latest-child",
            metadata={"thread_id": "stable-root"},
        )

        args, _stdin = cli.calls[0]
        assert args[args.index("--reply-to") + 1] == "stable-root"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stdout", "error_fragment"),
        [
            ("not json", "invalid CLI response"),
            (json.dumps([]), "invalid CLI response"),
            (json.dumps({"event_id": "evt"}), "invalid CLI response"),
            (json.dumps({"accepted": True}), "invalid CLI response"),
            (json.dumps({"accepted": True, "event_id": "   "}), "invalid CLI response"),
        ],
    )
    async def test_send_rejects_invalid_zero_exit_receipt(self, stdout, error_fragment):
        adapter = _make_adapter()
        adapter._run_cli = AsyncMock(return_value=(0, stdout, ""))

        result = await adapter.send(CHANNEL, "hello")

        assert result.success is False
        assert error_fragment in result.error
        assert result.message_id is None
        assert result.raw_response is None

    @pytest.mark.asyncio
    async def test_send_rejection_is_useful_and_bounded(self):
        adapter = _make_adapter()
        adapter._run_cli = AsyncMock(
            return_value=(
                0,
                json.dumps({"accepted": False, "message": "upload rejected " + "x" * 100_000}),
                "",
            )
        )

        result = await adapter.send(CHANNEL, "hello")

        assert result.success is False
        assert "upload rejected" in result.error
        assert len(result.error) <= 1024
        assert result.raw_response is None

    @pytest.mark.asyncio
    async def test_send_success_exposes_only_verified_receipt(self):
        adapter = _make_adapter()
        adapter._run_cli = AsyncMock(
            return_value=(
                0,
                json.dumps({
                    "accepted": True,
                    "event_id": " evt-safe ",
                    "message": "x" * 100_000,
                    "raw_response": "y" * 100_000,
                }),
                "",
            )
        )

        result = await adapter.send(CHANNEL, "hello")

        assert result.success is True
        assert result.message_id == "evt-safe"
        assert result.raw_response is None

    @pytest.mark.asyncio
    async def test_send_image_local_file_uses_file_flag(self, tmp_path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"\x89PNG fake")
        adapter = _make_adapter()
        cli = _ScriptedCli()
        cli.script("messages", "send", {"accepted": True, "event_id": "evt126", "message": ""})
        adapter._run_cli = cli
        result = await adapter.send_image(CHANNEL, str(img), caption="screenshot")
        assert result.success is True
        args, _stdin = cli.calls[0]
        assert args[args.index("--file") + 1] == str(img)

    @pytest.mark.asyncio
    async def test_media_directive_image_uses_file_upload(self, tmp_path, monkeypatch):
        image = _safe_media_file(tmp_path, monkeypatch, "preview.png", b"\x89PNG fake")
        adapter = _make_adapter()
        adapter._keep_typing = _hold_typing
        cli = _ScriptedCli()
        cli.script("messages", "send", {"accepted": True, "event_id": "evt-image", "message": ""})
        adapter._run_cli = cli

        async def handler(_event):
            return f"Preview attached.\nMEDIA:{image}"

        adapter.set_message_handler(handler)
        event = _message_event()
        await adapter._process_message_background(event, build_session_key(event.source))

        upload_calls = [args for args, _stdin in cli.calls if "--file" in args]
        assert len(upload_calls) == 1
        assert upload_calls[0][upload_calls[0].index("--file") + 1] == str(image)

    @pytest.mark.asyncio
    async def test_media_directive_document_uses_file_upload(self, tmp_path, monkeypatch):
        document = _safe_media_file(tmp_path, monkeypatch, "report.pdf", b"%PDF-1.4 fake")
        adapter = _make_adapter()
        adapter._keep_typing = _hold_typing
        cli = _ScriptedCli()
        cli.script("messages", "send", {"accepted": True, "event_id": "evt-document", "message": ""})
        adapter._run_cli = cli

        async def handler(_event):
            return f"Report attached.\nMEDIA:{document}"

        adapter.set_message_handler(handler)
        event = _message_event()
        await adapter._process_message_background(event, build_session_key(event.source))

        upload_calls = [args for args, _stdin in cli.calls if "--file" in args]
        assert len(upload_calls) == 1
        assert upload_calls[0][upload_calls[0].index("--file") + 1] == str(document)

    @pytest.mark.asyncio
    async def test_send_document_targets_metadata_thread(self, tmp_path):
        document = tmp_path / "report.pdf"
        document.write_bytes(b"%PDF-1.4 fake")
        adapter = _make_adapter()
        cli = _ScriptedCli()
        cli.script("messages", "send", {"accepted": True, "event_id": "evt-thread", "message": ""})
        adapter._run_cli = cli

        result = await adapter.send_document(
            CHANNEL,
            str(document),
            metadata={"thread_id": "thread-event"},
        )

        args, _stdin = cli.calls[0]
        assert args[args.index("--reply-to") + 1] == "thread-event"
        assert result.success is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("file_name", "media_type"),
        [
            ("artifact.html", "text/html"),
            ("artifact.zip", "application/zip"),
        ],
    )
    async def test_send_document_surfaces_relay_file_type_rejection(
        self,
        tmp_path,
        file_name,
        media_type,
    ):
        document = tmp_path / file_name
        document.write_bytes(b"unsupported")
        adapter = _make_adapter()
        cli = _ScriptedCli()
        cli.script(
            "messages",
            "send",
            "",
            code=1,
            stderr=(
                '{"error":"user_error","message":"unsupported file type: '
                f'{media_type}"}}'
            ),
        )
        adapter._run_cli = cli

        result = await adapter.send_document(CHANNEL, str(document), caption="artifact")

        args, _stdin = cli.calls[0]
        assert args[args.index("--file") + 1] == str(document)
        assert result.success is False
        assert f"unsupported file type: {media_type}" in result.error


# ── Lifecycle ─────────────────────────────────────────────────────────────

class TestBuzzAdapterLifecycle:

    @pytest.mark.asyncio
    async def test_disconnect_releases_scoped_lock(self, monkeypatch):
        """The identity lock taken in connect() must be released on disconnect."""
        import gateway.status as gateway_status

        released = []
        monkeypatch.setattr(
            gateway_status,
            "release_scoped_lock",
            lambda platform, key: released.append((platform, key)),
        )
        adapter = _make_adapter()
        adapter._platform_lock_scope = "buzz"
        adapter._platform_lock_identity = "wss://relay.example:" + SELF_PUBKEY
        await adapter.disconnect()
        assert released == [("buzz", "wss://relay.example:" + SELF_PUBKEY)]
        assert adapter._platform_lock_identity is None

    @pytest.mark.asyncio
    async def test_connect_fails_when_identity_lock_held(self, monkeypatch):
        """A second profile using the same relay+pubkey must fail fast. ``acquire_scoped_lock``
        returns ``(acquired, existing_record)`` — a bare truthiness check on that tuple never
        fires, so the mock returns the real contract."""
        import gateway.status as gateway_status

        monkeypatch.setattr(
            gateway_status, "acquire_scoped_lock",
            lambda scope, identity, metadata=None: (False, {"pid": 4242, "profile": "other"}),
        )
        adapter = _make_adapter()
        adapter.cli_path = "/fake/buzz"
        monkeypatch.setattr(_buzz_mod, "_resolve_private_key", lambda extra=None: "nsec1test")
        cli = _ScriptedCli()
        cli.script(
            "users", "get",
            [{"pubkey": SELF_PUBKEY, "display_name": "Chip"}],
        )
        adapter._run_cli = cli
        assert await adapter.connect() is False
        assert adapter._fatal_error_code == "buzz_lock"
        assert "other" in (adapter._fatal_error_message or "")
        assert adapter._platform_lock_identity == "https://test.relay:" + SELF_PUBKEY
        # channels list must never run: the conflict branch short-circuits connect()
        assert not any(call[0][:2] == ["channels", "list"] for call in cli.calls)

# ── Credentials / requirements ────────────────────────────────────────────

class TestCredentialResolution:

    def test_env_key_wins(self, monkeypatch):
        monkeypatch.setenv("BUZZ_PRIVATE_KEY", "nsec1fromenv")
        assert _resolve_private_key() == "nsec1fromenv"

    def test_credentials_file_fallback(self, monkeypatch, tmp_path):
        creds = tmp_path / "agent_credentials.json"
        creds.write_text(json.dumps({"nsec": "nsec1fromfile", "npub": "npub1x"}), encoding="utf-8")
        monkeypatch.setenv("BUZZ_CREDENTIALS_FILE", str(creds))
        assert _resolve_private_key() == "nsec1fromfile"

    def test_owner_auth_tag_from_credentials_file(self, monkeypatch, tmp_path):
        tag = ["auth", "b" * 64, "", "c" * 128]
        creds = tmp_path / "agent_credentials.json"
        creds.write_text(json.dumps({"nsec": "nsec1fromfile", "auth_tag": tag}), encoding="utf-8")
        monkeypatch.setenv("BUZZ_CREDENTIALS_FILE", str(creds))
        assert json.loads(_resolve_auth_tag()) == tag

    def test_invalid_owner_auth_tag_fails_closed(self, monkeypatch, tmp_path):
        creds = tmp_path / "agent_credentials.json"
        creds.write_text(json.dumps({"nsec": "nsec1fromfile", "auth_tag": ["bad"]}), encoding="utf-8")
        monkeypatch.setenv("BUZZ_CREDENTIALS_FILE", str(creds))
        with pytest.raises(ValueError, match="auth tag"):
            _resolve_auth_tag()

    def test_scoped_credentials_and_tag_do_not_borrow_ambient_profile(self, monkeypatch, tmp_path):
        from agent import secret_scope as ss

        ambient_tag = ["auth", "a" * 64, "", "d" * 128]
        scoped_tag = ["auth", "b" * 64, "", "c" * 128]
        ambient = tmp_path / "ambient.json"
        scoped = tmp_path / "scoped.json"
        ambient.write_text(json.dumps({"nsec": "nsec1ambient", "auth_tag": ambient_tag}))
        scoped.write_text(json.dumps({"nsec": "nsec1scoped", "auth_tag": scoped_tag}))
        monkeypatch.setenv("BUZZ_CREDENTIALS_FILE", str(ambient))
        monkeypatch.setenv("BUZZ_AUTH_TAG", json.dumps(ambient_tag))
        ss.set_multiplex_active(True)
        token = ss.set_secret_scope({"BUZZ_CREDENTIALS_FILE": str(scoped)})
        try:
            assert _resolve_private_key() == "nsec1scoped"
            assert json.loads(_resolve_auth_tag()) == scoped_tag
        finally:
            ss.reset_secret_scope(token)
            ss.set_multiplex_active(False)

    def test_owner_auth_tag_uses_credentials_autodiscovery(self, monkeypatch, tmp_path):
        tag = ["auth", "b" * 64, "", "c" * 128]
        creds = tmp_path / "agent_credentials.json"
        creds.write_text(json.dumps({"nsec": "nsec1fromfile", "auth_tag": tag}), encoding="utf-8")
        monkeypatch.setattr(_buzz_mod, "_DEFAULT_CREDENTIALS_DIR", tmp_path)
        assert _resolve_private_key() == "nsec1fromfile"
        assert json.loads(_resolve_auth_tag()) == tag

    def test_empty_multiplex_scope_never_autodiscovers_ambient_credentials(self, monkeypatch, tmp_path):
        from agent import secret_scope as ss

        tag = ["auth", "a" * 64, "", "d" * 128]
        (tmp_path / "general_credentials.json").write_text(
            json.dumps({"nsec": "nsec1ambient", "auth_tag": tag}), encoding="utf-8"
        )
        monkeypatch.setattr(_buzz_mod, "_DEFAULT_CREDENTIALS_DIR", tmp_path)
        ss.set_multiplex_active(True)
        token = ss.set_secret_scope({})
        try:
            assert _resolve_private_key() == ""
            assert _resolve_auth_tag() == ""
        finally:
            ss.reset_secret_scope(token)
            ss.set_multiplex_active(False)

# ── Env enablement / registration / standalone send ──────────────────────

class TestEnvEnablement:

    def test_returns_none_when_unconfigured(self):
        assert _env_enablement() is None

class TestBuzzPluginRegistration:

    def test_target_parser_accepts_channel_uuid_and_rejects_other_shapes(self):
        assert callable(_parse_target_ref)
        assert _parse_target_ref(f"  {CHANNEL.upper()}  ") == (CHANNEL, None)
        assert _parse_target_ref("general") is None
        assert _parse_target_ref("not-a-uuid") is None

    def test_register_platform_contract(self):
        from gateway.platform_registry import platform_registry

        platform_registry.unregister("buzz")
        ctx = MagicMock()
        register(ctx)
        ctx.register_platform.assert_called_once()
        kwargs = ctx.register_platform.call_args.kwargs
        assert kwargs["name"] == "buzz"
        assert kwargs["cron_deliver_env_var"] == "BUZZ_HOME_CHANNEL"
        assert kwargs["allowed_users_env"] == "BUZZ_ALLOWED_USERS"
        assert kwargs["allow_all_env"] == "BUZZ_ALLOW_ALL_USERS"
        assert callable(kwargs["standalone_sender_fn"])
        assert callable(kwargs["env_enablement_fn"])
        assert kwargs["parse_target_ref_fn"] is _parse_target_ref
        assert set(kwargs["required_env"]) == {"BUZZ_RELAY_URL", "BUZZ_PRIVATE_KEY"}

class TestStandaloneSend:

    @pytest.mark.asyncio
    async def test_standalone_send_success(self, monkeypatch, tmp_path):
        from gateway.config import PlatformConfig

        fake_cli = tmp_path / "buzz"
        fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setenv("BUZZ_RELAY_URL", "https://r")
        monkeypatch.setenv("BUZZ_PRIVATE_KEY", "nsec1x")
        monkeypatch.setenv("BUZZ_CLI_PATH", str(fake_cli))

        captured = {}

        async def fake_exec(cli_path, args, *, relay_url, private_key, auth_tag="", input_text=None, timeout=30.0):
            captured.update(cli_path=cli_path, args=args, relay_url=relay_url, auth_tag=auth_tag, input_text=input_text)
            return 0, json.dumps({"accepted": True, "event_id": "evt-cron", "message": ""}), ""

        monkeypatch.setattr(_buzz_mod, "_exec_buzz", fake_exec)

        result = await _standalone_send(PlatformConfig(enabled=True, extra={}), CHANNEL, "cron says hi")
        assert result == {"success": True, "message_id": "evt-cron"}
        assert captured["args"][:2] == ["messages", "send"]
        assert captured["input_text"] == "cron says hi"
        # The private key must never be part of argv
        assert all("nsec1x" not in str(a) for a in captured["args"])

    @pytest.mark.asyncio
    async def test_standalone_send_injects_owner_auth_tag_from_credentials_file(
        self, monkeypatch, tmp_path
    ):
        """Cron/standalone path must load NIP-OA auth_tag from credentials JSON.

        Main-line regression: when only BUZZ_PRIVATE_KEY is ambient and the
        credentials file holds auth_tag, omitting injection causes relay 403
        membership failures on owner-gated relays.
        """
        from gateway.config import PlatformConfig

        tag = ["auth", "b" * 64, "", "c" * 128]
        creds = tmp_path / "agent_credentials.json"
        creds.write_text(
            json.dumps({"nsec": "nsec1fromfile", "auth_tag": tag}),
            encoding="utf-8",
        )
        fake_cli = tmp_path / "buzz"
        fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setenv("BUZZ_RELAY_URL", "https://r")
        monkeypatch.setenv("BUZZ_CLI_PATH", str(fake_cli))
        monkeypatch.setenv("BUZZ_CREDENTIALS_FILE", str(creds))
        monkeypatch.delenv("BUZZ_PRIVATE_KEY", raising=False)
        monkeypatch.delenv("BUZZ_AUTH_TAG", raising=False)

        captured = {}

        async def fake_exec(
            cli_path, args, *, relay_url, private_key, auth_tag="", input_text=None, timeout=30.0
        ):
            captured.update(
                private_key=private_key,
                auth_tag=auth_tag,
                args=args,
                input_text=input_text,
            )
            return 0, json.dumps({"accepted": True, "event_id": "evt-auth", "message": ""}), ""

        monkeypatch.setattr(_buzz_mod, "_exec_buzz", fake_exec)

        result = await _standalone_send(
            PlatformConfig(enabled=True, extra={}), CHANNEL, "cron needs owner auth"
        )
        assert result == {"success": True, "message_id": "evt-auth"}
        assert captured["private_key"] == "nsec1fromfile"
        assert json.loads(captured["auth_tag"]) == tag
        # Secrets stay out of argv (auth_tag is env-injected by _exec_buzz).
        joined_args = " ".join(str(a) for a in captured["args"])
        assert "nsec1fromfile" not in joined_args
        assert tag[1] not in joined_args
        assert tag[3] not in joined_args

    @pytest.mark.asyncio
    async def test_standalone_send_key_only_does_not_invent_auth_tag(
        self, monkeypatch, tmp_path
    ):
        """Direct private key without credentials_file must not invent an auth tag."""
        from gateway.config import PlatformConfig

        fake_cli = tmp_path / "buzz"
        fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setenv("BUZZ_RELAY_URL", "https://r")
        monkeypatch.setenv("BUZZ_PRIVATE_KEY", "nsec1x")
        monkeypatch.setenv("BUZZ_CLI_PATH", str(fake_cli))
        monkeypatch.delenv("BUZZ_AUTH_TAG", raising=False)
        monkeypatch.delenv("BUZZ_CREDENTIALS_FILE", raising=False)
        # Ambient credentials dir must not be borrowed when a direct key is set.
        ambient = tmp_path / "ambient_credentials.json"
        ambient.write_text(
            json.dumps({"nsec": "nsec1ambient", "auth_tag": ["auth", "a" * 64, "", "d" * 128]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(_buzz_mod, "_DEFAULT_CREDENTIALS_DIR", tmp_path)

        captured = {}

        async def fake_exec(
            cli_path, args, *, relay_url, private_key, auth_tag="", input_text=None, timeout=30.0
        ):
            captured.update(private_key=private_key, auth_tag=auth_tag)
            return 0, json.dumps({"accepted": True, "event_id": "evt-key", "message": ""}), ""

        monkeypatch.setattr(_buzz_mod, "_exec_buzz", fake_exec)
        result = await _standalone_send(
            PlatformConfig(enabled=True, extra={}), CHANNEL, "key only"
        )
        assert result == {"success": True, "message_id": "evt-key"}
        assert captured["private_key"] == "nsec1x"
        assert captured["auth_tag"] == ""

    @pytest.mark.asyncio
    async def test_standalone_send_retries_unresolved_presentation_mention(
        self, monkeypatch, tmp_path
    ):
        from gateway.config import PlatformConfig

        fake_cli = tmp_path / "buzz"
        fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setenv("BUZZ_RELAY_URL", "https://r")
        monkeypatch.setenv("BUZZ_PRIVATE_KEY", "nsec1x")
        monkeypatch.setenv("BUZZ_CLI_PATH", str(fake_cli))
        sent = []

        async def fake_exec(
            cli_path,
            args,
            *,
            relay_url,
            private_key,
            auth_tag="",
            input_text=None,
            timeout=30.0,
        ):
            sent.append(input_text)
            if len(sent) == 1:
                return (
                    1,
                    "",
                    "user_error: mention '@session' does not match a current "
                    "channel member; retry with --mention <pubkey>",
                )
            return (
                0,
                json.dumps(
                    {"accepted": True, "event_id": "evt-standalone", "message": ""}
                ),
                "",
            )

        monkeypatch.setattr(_buzz_mod, "_exec_buzz", fake_exec)

        result = await _standalone_send(
            PlatformConfig(enabled=True, extra={}),
            CHANNEL,
            "See @session:default/example.",
        )

        assert result == {"success": True, "message_id": "evt-standalone"}
        assert sent == [
            "See @session:default/example.",
            "See @\u200bsession:default/example.",
        ]

    @pytest.mark.asyncio
    async def test_standalone_send_extracts_path_from_media_descriptor(self, monkeypatch, tmp_path):
        from gateway.config import PlatformConfig

        fake_cli = tmp_path / "buzz"
        fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
        document = tmp_path / "report.txt"
        document.write_text("report", encoding="utf-8")
        monkeypatch.setenv("BUZZ_RELAY_URL", "https://r")
        monkeypatch.setenv("BUZZ_PRIVATE_KEY", "nsec1x")
        monkeypatch.setenv("BUZZ_CLI_PATH", str(fake_cli))

        captured = {}

        async def fake_exec(cli_path, args, *, relay_url, private_key, auth_tag="", input_text=None, timeout=30.0):
            captured["args"] = args
            return 0, json.dumps({"accepted": True, "event_id": "evt-media"}), ""

        monkeypatch.setattr(_buzz_mod, "_exec_buzz", fake_exec)

        result = await _standalone_send(
            PlatformConfig(enabled=True, extra={}),
            CHANNEL,
            "attached",
            media_files=[(str(document), False)],
        )

        assert result == {
            "success": True,
            "message_id": "evt-media",
            "media_delivered": True,
        }
        file_index = captured["args"].index("--file")
        assert captured["args"][file_index + 1] == str(document)

# ── Editing and deleting (streaming) ──────────────────────────────────

class TestBuzzAdapterEdit:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("content", ["partial answer\nGrüezi 🌍\n", "--status\n- checking progress"])
    async def test_edit_preserves_literal_content_and_original_target(self, content):
        adapter = _make_adapter()
        adapter._channel_state[CHANNEL] = {"chat_type": "group", "last_ts": 0, "seen": {}}
        cli = _ScriptedCli()
        cli.script("messages", "edit", {"accepted": True, "event_id": "edit1", "message": ""})
        adapter._run_cli = cli

        result = await adapter.edit_message(CHANNEL, "orig1", content)
        assert result.success is True
        assert result.message_id == "orig1"

        args, stdin_text = cli.calls[0]
        assert args[:2] == ["messages", "edit"]
        # Unlike ``messages send``, ``messages edit`` takes ``--content`` literally (no stdin); the ``=``
        # form keeps hyphen-leading replacement text from being parsed as a flag.
        assert args[2:] == ["--event", "orig1", f"--content={content}"]
        assert stdin_text is None

    @pytest.mark.asyncio
    async def test_edit_marks_its_own_event_seen(self):
        adapter = _make_adapter()
        adapter._channel_state[CHANNEL] = {"chat_type": "group", "last_ts": 0, "seen": {}}
        cli = _ScriptedCli()
        cli.script("messages", "edit", {"accepted": True, "event_id": "edit1"})
        adapter._run_cli = cli

        await adapter.edit_message(CHANNEL, "orig1", "text")
        assert "edit1" in adapter._channel_state[CHANNEL]["seen"]

    @pytest.mark.asyncio
    async def test_edit_accepts_finalize_without_changing_behaviour(self):
        adapter = _make_adapter()
        adapter._channel_state[CHANNEL] = {"chat_type": "group", "last_ts": 0, "seen": {}}
        cli = _ScriptedCli()
        cli.script("messages", "edit", {"accepted": True, "event_id": "edit1"})
        adapter._run_cli = cli

        result = await adapter.edit_message(CHANNEL, "orig1", "text", finalize=True)
        assert result.success is True
        assert len(cli.calls) == 1

    @pytest.mark.asyncio
    async def test_edit_without_a_message_id_never_calls_the_cli(self):
        adapter = _make_adapter()
        cli = _ScriptedCli()
        adapter._run_cli = cli

        result = await adapter.edit_message(CHANNEL, "", "text")
        assert result.success is False
        assert cli.calls == []

    @pytest.mark.asyncio
    async def test_edit_with_empty_content_never_calls_the_cli(self):
        adapter = _make_adapter()
        cli = _ScriptedCli()
        adapter._run_cli = cli

        result = await adapter.edit_message(CHANNEL, "orig1", "")
        assert result.success is False
        assert cli.calls == []

    @pytest.mark.asyncio
    async def test_edit_relay_error_is_retryable_but_bad_input_is_not(self):
        adapter = _make_adapter()
        cli = _ScriptedCli()
        cli.script("messages", "edit", "", code=2, stderr="relay unreachable")
        adapter._run_cli = cli
        relay_failure = await adapter.edit_message(CHANNEL, "orig1", "text")
        assert relay_failure.success is False
        assert relay_failure.retryable is True

        adapter = _make_adapter()
        cli = _ScriptedCli()
        cli.script("messages", "edit", "", code=1, stderr="bad input")
        adapter._run_cli = cli
        input_failure = await adapter.edit_message(CHANNEL, "orig1", "text")
        assert input_failure.success is False
        assert input_failure.retryable is False

    @pytest.mark.asyncio
    async def test_delete_targets_the_event(self):
        adapter = _make_adapter()
        adapter._channel_state[CHANNEL] = {"chat_type": "group", "last_ts": 0, "seen": {}}
        cli = _ScriptedCli()
        cli.script("messages", "delete", {"accepted": True, "event_id": "del1"})
        adapter._run_cli = cli

        assert await adapter.delete_message(CHANNEL, "orig1") is True
        assert cli.calls[0][0] == ["messages", "delete", "--event", "orig1"]

    @pytest.mark.asyncio
    async def test_delete_failure_returns_false(self):
        adapter = _make_adapter()
        cli = _ScriptedCli()
        cli.script("messages", "delete", "", code=2, stderr="relay unreachable")
        adapter._run_cli = cli

        assert await adapter.delete_message(CHANNEL, "orig1") is False

    @pytest.mark.asyncio
    async def test_delete_without_a_message_id_never_calls_the_cli(self):
        adapter = _make_adapter()
        cli = _ScriptedCli()
        adapter._run_cli = cli

        assert await adapter.delete_message(CHANNEL, "") is False
        assert cli.calls == []
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stdout",
        [
            "not json",
            json.dumps([]),
            json.dumps({"event_id": "evt"}),
            json.dumps({"accepted": True}),
            json.dumps({"accepted": True, "event_id": ""}),
        ],
    )
    async def test_standalone_send_rejects_invalid_zero_exit_receipt(
        self, monkeypatch, tmp_path, stdout
    ):
        from gateway.config import PlatformConfig

        fake_cli = tmp_path / "buzz"
        fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setenv("BUZZ_RELAY_URL", "https://r")
        monkeypatch.setenv("BUZZ_PRIVATE_KEY", "nsec1x")
        monkeypatch.setenv("BUZZ_CLI_PATH", str(fake_cli))
        monkeypatch.setattr(
            _buzz_mod,
            "_exec_buzz",
            AsyncMock(return_value=(0, stdout, "")),
        )

        result = await _standalone_send(
            PlatformConfig(enabled=True, extra={}), CHANNEL, "hello"
        )

        assert result.get("error")
        assert "success" not in result

    @pytest.mark.asyncio
    async def test_standalone_send_rejection_is_useful_bounded_and_not_delivered(
        self, monkeypatch, tmp_path
    ):
        from gateway.config import PlatformConfig

        fake_cli = tmp_path / "buzz"
        fake_cli.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setenv("BUZZ_RELAY_URL", "https://r")
        monkeypatch.setenv("BUZZ_PRIVATE_KEY", "nsec1x")
        monkeypatch.setenv("BUZZ_CLI_PATH", str(fake_cli))
        monkeypatch.setattr(
            _buzz_mod,
            "_exec_buzz",
            AsyncMock(
                return_value=(
                    0,
                    json.dumps({"accepted": False, "message": "upload rejected " + "x" * 100_000}),
                    "",
                )
            ),
        )

        result = await _standalone_send(
            PlatformConfig(enabled=True, extra={}),
            CHANNEL,
            "hello",
            media_files=[("/tmp/report.txt", False)],
        )

        assert "upload rejected" in result["error"]
        assert len(result["error"]) <= 1024
        assert "success" not in result
        assert "media_delivered" not in result
# ── Durable channel cursors across restart (#90464) ───────────────────────

class TestChannelCursorPersistence:
    """A restart must resume from the saved cursor, not reseed from history.

    Seeding marks every event currently in the channel as seen. Anything that
    arrived while the gateway was down is in that history, so an unconditional
    reseed swallows it permanently even though the relay still has it.
    """

    @pytest.fixture
    def adapter(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        a = _make_adapter()
        a._dispatched = []

        async def capture(**kwargs):
            a._dispatched.append(kwargs)

        a._dispatch_message = capture
        a._message_handler = AsyncMock()
        return a

    @staticmethod
    def _cursor_file(tmp_path):
        return tmp_path / "buzz" / "channel-cursors.json"

    async def _seed(self, adapter, *events):
        cli = _ScriptedCli()
        cli.script("messages", "get", list(events))
        adapter._run_cli = cli
        adapter._load_cursors()
        await adapter._seed_channel(CHANNEL, chat_type="group")
        adapter._save_cursors()
        return cli

    @pytest.mark.asyncio
    async def test_restart_resumes_instead_of_reseeding(self, adapter, tmp_path, monkeypatch):
        await self._seed(adapter, _event("e1", content="@Chip first", created_at=100))

        # Restart. The relay now also holds a mention that landed while the
        # gateway was down, and it sits in the same history a reseed reads.
        restarted = _make_adapter()
        restarted._dispatched = []

        async def capture(**kwargs):
            restarted._dispatched.append(kwargs)

        restarted._dispatch_message = capture
        restarted._message_handler = AsyncMock()
        cli = _ScriptedCli()
        cli.script("messages", "get", [
            _event("e1", content="@Chip first", created_at=100),
            _event("e2", content="@Chip sent while you were down", created_at=150),
        ])
        restarted._run_cli = cli
        restarted._load_cursors()
        await restarted._seed_channel(CHANNEL, chat_type="group")

        # Restoring must not spend a CLI call on history it is not going to use.
        assert cli.calls == []
        state = restarted._channel_state[CHANNEL]
        assert set(state["seen"]) == {"e1"}
        assert state["last_ts"] == 100

        # The first poll after the restart delivers the missed mention.
        await restarted._poll_channel(CHANNEL)
        assert [d["message_id"] for d in restarted._dispatched] == ["e2"]

    @pytest.mark.asyncio
    async def test_cursor_survives_only_for_the_same_identity_and_relay(self, adapter, tmp_path, monkeypatch):
        await self._seed(adapter, _event("e1", created_at=100))

        # Same machine, different bot: channel ids would collide but the event
        # stream behind them is another one, so the cursor must be ignored.
        other = _make_adapter()
        other._self_pubkey = OTHER_PUBKEY
        other._load_cursors()
        assert other._restored_cursors == {}

        elsewhere = _make_adapter({"relay_url": "https://other.relay"})
        elsewhere._load_cursors()
        assert elsewhere._restored_cursors == {}

    @pytest.mark.asyncio
    async def test_unreadable_cursor_file_falls_back_to_seeding(self, adapter, tmp_path):
        path = self._cursor_file(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")

        cli = _ScriptedCli()
        cli.script("messages", "get", [_event("e1", created_at=100)])
        adapter._run_cli = cli
        adapter._load_cursors()
        await adapter._seed_channel(CHANNEL, chat_type="group")

        # Degrades to the old behaviour rather than failing the connect.
        assert adapter._restored_cursors == {}
        assert set(adapter._channel_state[CHANNEL]["seen"]) == {"e1"}

    @pytest.mark.asyncio
    async def test_restored_seen_set_stays_bounded(self, adapter, tmp_path):
        cap = _buzz_mod._SEEN_CAP
        path = self._cursor_file(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "identity": SELF_PUBKEY,
                "relay": "https://test.relay",
                "channels": {
                    CHANNEL: {
                        "chat_type": "group",
                        "last_ts": 100,
                        "seen": [f"e{i}" for i in range(cap * 2)],
                    }
                },
            }),
            encoding="utf-8",
        )
        adapter._load_cursors()
        await adapter._seed_channel(CHANNEL, chat_type="group")

        seen = adapter._channel_state[CHANNEL]["seen"]
        assert len(seen) == cap
        # The newest ids are the ones worth keeping for de-dupe.
        assert f"e{cap * 2 - 1}" in seen
        assert "e0" not in seen

    @pytest.mark.asyncio
    async def test_cursor_write_runs_off_the_event_loop(self, adapter, tmp_path, monkeypatch):
        """The write fsyncs + renames once per inbound event on the WebSocket transport."""
        import threading

        import utils

        cli = await self._seed(adapter, _event("e1", created_at=100))
        real_write = utils.atomic_json_write
        threads = []

        def _record(*args, **kwargs):
            threads.append(threading.get_ident())
            return real_write(*args, **kwargs)

        monkeypatch.setattr(utils, "atomic_json_write", _record)
        cli.responses.clear()
        cli.script("messages", "get", [_event("e1", created_at=100), _event("e2", created_at=200)])
        await adapter._poll_channel(CHANNEL)

        assert threads and threading.get_ident() not in threads
        saved = json.loads(self._cursor_file(tmp_path).read_text(encoding="utf-8"))
        assert saved["channels"][CHANNEL]["last_ts"] == 200
        assert saved["channels"][CHANNEL]["seen"] == ["e1", "e2"]
