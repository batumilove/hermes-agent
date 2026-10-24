"""Fallback route activation diagnostics."""
from __future__ import annotations
from agent import chat_completion_helpers as _source

def _log_fallback_activated(agent, reason, old_model, old_provider, fb_model, fb_provider) -> None:
    """A billing switch is a WARNING naming the profile, both models and the remedy: the gateway
    persists the turn as a transient failure otherwise, and nothing in the log says the paid
    model was refused for credits or how to fix it (#115702). Other reasons stay INFO."""
    if reason != _source.FailoverReason.billing:
        _source.logger.info("Fallback activated: %s → %s (%s)", old_model, fb_model, fb_provider)
        return
    from hermes_constants import get_hermes_home, profile_name_for_home
    profile = profile_name_for_home(get_hermes_home()) or "default"
    remedy = "hermes model" if profile == "default" else f"hermes -p {profile} model"
    _source.logger.warning(
        "Profile %s: %s via %s refused for billing/credits — using fallback %s via %s. "
        "Top up credits, or run `%s` to pick a model this account can use.",
        profile, old_model, old_provider, fb_model, fb_provider, remedy,
    )
