"""Authorized Buzz attachment transport and bounded quoted-parent context."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import hashlib
import time
from typing import Optional
from urllib.parse import urlsplit

from gateway.platforms.base import CachedMedia, cache_media_bytes_async


class BuzzAttachmentMixin:
    """Keep attachment work in the identity and authorization scope of its adapter."""

    async def _download_attachment(self, metadata: dict) -> Optional[CachedMedia]:
        """Download, integrity-check, and cache one authorized Buzz attachment."""
        api = self._attachment_runtime
        url = metadata["url"]
        try:
            parsed_url = urlsplit(url)
            origin = ((parsed_url.hostname or "").lower().rstrip("."), 443 if parsed_url.port is None else parsed_url.port)
        except ValueError:
            parsed_url, origin = None, ("", 0)
        if (parsed_url is None or parsed_url.scheme != "https" or origin not in self._attachment_origins
                or parsed_url.username or parsed_url.password or parsed_url.fragment):
            api.logger.warning("Buzz: refusing attachment from untrusted origin %s:%s", origin[0] or "<missing>", origin[1])
            return None
        path_match = api._MEDIA_PATH_RE.fullmatch(parsed_url.path)
        if path_match and path_match.group("sha").lower() != metadata["sha256"]:
            api.logger.warning("Buzz: attachment URL digest does not match imeta")
            return None
        if not 0 < metadata["size"] <= api._MAX_INBOUND_ATTACHMENT_BYTES:
            return None
        import httpx
        try:
            headers = {"Accept-Encoding": "identity"}
            relay = urlsplit(self.relay_url)
            relay_origin = ((relay.hostname or "").lower().rstrip("."), 443 if relay.port is None else relay.port)
            # Only canonical same-relay objects receive credentials. Optional CDN hosts stay anonymous.
            if relay.scheme in {"https", "wss"} and origin == relay_origin and path_match:
                if parsed_url.query:
                    return None
                if not self._private_key:
                    self._private_key = api._resolve_private_key(self._extra)
                    self._auth_tag = api._resolve_auth_tag(self._extra)
                headers["Authorization"] = api._nostr_auth.build_media_get_authorization(
                    private_key=self._private_key, server=relay.netloc,
                )
                if self._auth_tag:
                    headers["x-auth-tag"] = self._auth_tag
            timeout = httpx.Timeout(api._ATTACHMENT_DOWNLOAD_TIMEOUT)
            async with (
                asyncio.timeout(api._ATTACHMENT_DOWNLOAD_TIMEOUT),
                httpx.AsyncClient(follow_redirects=False, timeout=timeout, headers=headers) as client,
                client.stream("GET", url) as response,
            ):
                if response.status_code != 200:
                    api.logger.warning("Buzz: attachment download returned HTTP %s", response.status_code)
                    return None
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    return None
                if content_length := response.headers.get("content-length"):
                    try:
                        declared_response_size = int(content_length)
                    except ValueError:
                        return None
                    if declared_response_size != metadata["size"]:
                        api.logger.warning("Buzz: attachment Content-Length does not match imeta size")
                        return None
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                    if len(data) + len(chunk) > metadata["size"]:
                        api.logger.warning("Buzz: attachment exceeded its declared size")
                        return None
                    data.extend(chunk)
        except (TimeoutError, httpx.HTTPError, OSError, ValueError) as exc:
            api.logger.warning("Buzz: attachment download failed: %s", exc)
            return None
        for bad, what in ((len(data) != metadata["size"], "size"), (hashlib.sha256(data).hexdigest() != metadata["sha256"], "SHA-256")):
            if bad:
                api.logger.warning("Buzz: attachment %s does not match imeta", what)
                return None
        try:
            return await cache_media_bytes_async(bytes(data), filename=metadata["filename"], mime_type=metadata["mime_type"])
        except (OSError, ValueError) as exc:
            api.logger.warning("Buzz: attachment cache write failed: %s", exc)
            return None

    async def _cache_inbound_attachments(self, metadata_items: list[dict]) -> list[CachedMedia]:
        return [a for m in metadata_items if (a := await self._download_attachment(m)) is not None]

    def _remember_event(self, state: dict, event: dict, *, attachment_authorized: Optional[bool] = None) -> None:
        """Record author + content snippet for later NIP-10 parent lookup."""
        api = self._attachment_runtime
        event_id = str(event.get("id") or "")
        content = event.get("content")
        if event_id:
            snippet = content[:api._EVENT_META_CONTENT_CAP] if isinstance(content, str) else ""
            author = str(event.get("pubkey") or "").lower()
            self._store_event_meta(state, event_id, author, snippet)
            cache = state.setdefault("quoted_attachments", OrderedDict())
            # Invalidate a previous observation before considering this exact event.
            cache.pop(event_id, None)
            if attachment_authorized is False or int(event.get("kind") or 0) not in api._DISPATCH_KINDS:
                return
            tags = event.get("tags")
            channels = [tag[1] for tag in (tags if isinstance(tags, list) else [])
                        if isinstance(tag, (list, tuple)) and len(tag) >= 2 and tag[0] == "h"]
            if len(channels) != 1 or not isinstance(channels[0], str):
                return
            channel = channels[0]
            chat_type = state.get("chat_type")
            if ((self._allowed_pubkeys and author not in self._allowed_pubkeys)
                    or (attachment_authorized is not True
                        and self._is_sender_authorized(author, chat_type, channel) is not True)):
                return
            items = self._imeta_attachments(event)
            # Bound retained strings as well as object/byte counts; never retain raw tags.
            items = [dict(item) for item in items if len(item["url"]) <= 4096]
            if items:
                cache[event_id] = (channel, author, time.monotonic(), items)
                while len(cache) > self._THREAD_ROOT_CACHE:
                    cache.popitem(last=False)

    def _quoted_attachment_metadata(self, state: dict, parent_id: Optional[str], channel: str,
                                    chat_type: str, child_author: str) -> list[dict]:
        """Return bounded imeta only under original and current parent authorization."""
        cache = state.get("quoted_attachments", {})
        entry = cache.get(parent_id)
        if not entry:
            return []
        parent_channel, author, observed_at, items = entry
        if time.monotonic() - observed_at > 900:
            cache.pop(parent_id, None)
            return []
        meta = self._lookup_event_meta(state, parent_id)
        if (parent_channel != channel or not meta or meta[0] != author
                or (self._allowed_pubkeys and author not in self._allowed_pubkeys)
                or self._is_sender_authorized(child_author, chat_type, channel) is not True
                or self._is_sender_authorized(author, chat_type, channel) is not True):
            return []
        return [dict(item) for item in items]

    async def _collect_inbound_attachments(
        self, state: dict, event: dict, attachment_metadata: list[dict], parent_id: Optional[str],
        channel: str, chat_type: str, author: str,
    ) -> tuple[list[CachedMedia], int, bool]:
        """Use one metadata budget for current and authorized quoted attachments."""
        api = self._attachment_runtime
        fetch_allowed = bool(attachment_metadata) and self._is_sender_authorized(author, chat_type, channel) is True
        if fetch_allowed:
            self._remember_event(state, event, attachment_authorized=True)
        attachments = await self._cache_inbound_attachments(attachment_metadata) if fetch_allowed else []
        current_failed = len(attachment_metadata) - len(attachments) if fetch_allowed else 0
        # Quotes are not grants: require both identities and the exact channel-bound,
        # unexpired metadata captured from the original event. Never fetch reply text.
        parent_items = self._quoted_attachment_metadata(state, parent_id, channel, chat_type, author)
        used = {(m["sha256"], m["url"]) for m in attachment_metadata}
        declared_bytes = sum(m["size"] for m in attachment_metadata)
        count = len(attachment_metadata)
        selected = []
        for item in parent_items:
            identity = (item["sha256"], item["url"])
            if identity in used:
                continue
            if count >= api._MAX_INBOUND_ATTACHMENTS or declared_bytes + item["size"] > api._MAX_INBOUND_ATTACHMENT_BYTES:
                continue
            selected.append(item)
            used.add(identity)
            count += 1
            declared_bytes += item["size"]
        quoted_failed = False
        if selected:
            parent_attachments = await self._cache_inbound_attachments(selected)
            attachments.extend(parent_attachments)
            quoted_failed = len(parent_attachments) != len(selected)
        return attachments, current_failed, quoted_failed
