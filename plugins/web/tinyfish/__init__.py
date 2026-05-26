"""TinyFish web search + fetch plugin — bundled, auto-loaded."""
from __future__ import annotations

from plugins.web.tinyfish.provider import TinyFishWebSearchProvider


def register(ctx) -> None:
    """Register the TinyFish provider with the plugin context."""
    ctx.register_web_search_provider(TinyFishWebSearchProvider())
