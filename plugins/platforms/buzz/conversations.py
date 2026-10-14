"""Buzz conversation directory, direct-message classification and mention policy.

A signed recipient tag permits DM-shaped conversations to latch. Hosted relays
that omit that tag additionally require an explicitly allowed sender; channel
metadata and allow-all mode remain behind the normal mention gate.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger("plugins.platforms.buzz.adapter")


class BuzzConversationMixin:
    """Use relay metadata without weakening private sender authorization."""

    async def list_channels(self) -> Optional[list[dict[str, str]]]:
        """Expose relay-discovered conversations to Hermes' channel directory."""
        if not self._channel_names:
            return None
        return [
            {
                "id": channel_id,
                "name": name,
                "type": self._channel_state.get(channel_id, {}).get("chat_type", "group"),
            }
            for channel_id, name in self._channel_names.items()
        ]

    def _may_reclassify_as_dm(self, channel_id: str) -> bool:
        """True when metadata does not rule out a DM (name "DM", empty description); missing metadata fails closed."""
        meta = self._channel_meta.get(channel_id)
        if meta is None:
            return False
        return str(meta.get("name") or "").strip() == "DM" and not str(meta.get("description") or "").strip()

    def _is_direct_message_event(self, channel_id: str, event: dict) -> bool:
        """True when ``event`` is safely attributable to a DM conversation.

        Prefer the structural p-tag used by most hosted relays. Some relays
        materialize the conversation as ``name=DM`` but omit that p-tag from
        kind-9 messages; for those, trust the DM metadata only for an explicitly
        allow-listed sender. This preserves mention gating for community-wide
        access and for channel-like metadata.
        """
        if not self._self_pubkey or not self._may_reclassify_as_dm(channel_id):
            return False
        if int(event.get("kind") or 0) != 9:
            return False
        pubkey = str(event.get("pubkey") or "").lower()
        if not pubkey or pubkey == self._self_pubkey:
            return False
        content = event.get("content")
        if not isinstance(content, str) or self._is_mentioned(content):
            return False

        # Private-mode fallback for hosted relays that omit structural p-tags
        # from DM messages. Never broaden this to allow-all mode: a channel
        # named "DM" must not bypass mention gating for arbitrary members.
        if self._allowed_pubkeys and pubkey in self._allowed_pubkeys:
            return True

        tags = event.get("tags")
        if not isinstance(tags, list):
            return False
        return any(
            isinstance(tag, (list, tuple))
            and len(tag) > 1
            and tag[0] == "p"
            and str(tag[1]).lower() == self._self_pubkey
            for tag in tags
        )

    def _maybe_latch_dm(self, channel_id: str, state: dict, event: dict) -> None:
        """Latch a group conversation to "dm" once a direct message is seen; it sticks."""
        if state["chat_type"] == "dm" or not self._is_direct_message_event(channel_id, event):
            return
        state["chat_type"] = "dm"
        self._channel_names.setdefault(channel_id, "DM")
        logger.info("Buzz: conversation %s reclassified as DM", channel_id)

    def _is_mentioned(self, content: str) -> bool:
        """True when text explicitly addresses this agent (npub, hex, or @name)."""
        lowered = content.lower()
        patterns = []
        if self._self_pubkey and re.fullmatch(r"[0-9a-f]{64}", self._self_pubkey):
            patterns.append(rf"(?<![0-9a-f]){re.escape(self._self_pubkey)}(?![0-9a-f])")
        if self._self_npub:
            patterns.append(rf"(?<![a-z0-9]){re.escape(self._self_npub.lower())}(?![a-z0-9])")
        if self._display_name:
            patterns.append(rf"(?<![\w@])@{re.escape(self._display_name.lower())}" + r"(?=$|[\s,;.!?:)\]}])")
        return any(re.search(p, lowered) for p in patterns)

    def _is_addressed(self, event: dict) -> bool:
        """True when a group event carries an explicit text or p-tag address."""
        content = event.get("content")
        return isinstance(content, str) and (self._is_mentioned(content) or self._p_tagged_to_self(event))

    def _strip_mention(self, content: str) -> str:
        """Strip a LEADING @mention of this agent so ``is_command()`` sees "/whoami"; mid-sentence mentions stay."""
        text = content.strip()
        # Display names require '@'; npub/hex identities are unambiguous without it.
        candidates = []
        if self._display_name:
            candidates.append(rf"@{re.escape(self._display_name)}" + r"(?=$|[\s,;.!?:)\]}])")
        if self._self_npub:
            candidates.append(rf"@?{re.escape(self._self_npub)}(?![a-z0-9])")
        if self._self_pubkey:
            candidates.append(rf"@?{re.escape(self._self_pubkey)}(?![0-9a-f])")
        if not candidates:
            return text
        return re.sub(rf"^(?:{'|'.join(candidates)})[\s:,]*", "", text, count=1, flags=re.IGNORECASE).strip()
