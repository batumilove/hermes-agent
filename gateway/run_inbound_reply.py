"""Gateway prepend inbound reply context contract."""
from __future__ import annotations
from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource

def _prepend_inbound_reply_context(event: MessageEvent, source: SessionSource, message_text: str) -> str:
    from gateway.run_inbound import discord_triggering_note
    """Prepend the reply-to pointer, then the Discord triggering-message note (outermost)."""
    if getattr(event, "reply_to_text", None) and event.reply_to_message_id:
        # Always inject the reply-to pointer even when the quoted text is already in history:
        # it's disambiguation (*which* prior message), not deduplication.
        # Preserve the complete adapter-resolved context, including native partial quotes.
        # Clipping can hide the later list item or code the user is referencing.
        reply_text = event.reply_to_text
        _who = " your previous message" if getattr(event, "reply_to_is_own_message", False) else ""
        message_text = f'[Replying to{_who}: "{reply_text}"]\n\n{message_text}'

    # Discord: the triggering message id goes on the per-turn user message, never the cached
    # system prompt — it changes every turn and would bust the agent-cache signature. It is
    # the OUTERMOST prefix so strip_discord_triggering_note can peel exactly it off the
    # persisted transcript row without touching the reply pointer.
    if (
        source is not None
        and getattr(source, "platform", None) == Platform.DISCORD
        and getattr(event, "message_id", None)
    ):
        from gateway.session import _discord_tools_loaded as _disc_tools_loaded
        if _disc_tools_loaded():
            message_text = f"{discord_triggering_note(event.message_id)}\n\n{message_text}"
    return message_text
