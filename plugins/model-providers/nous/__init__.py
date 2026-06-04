"""Nous Portal provider profile."""

from typing import Any

from agent.portal_tags import nous_portal_tags
from providers import register_provider
from providers.base import ProviderProfile


class NousProfile(ProviderProfile):
    """Nous Portal — product tags, reasoning with Nous-specific omission."""

    def build_extra_body(
        self, *, session_id: str | None = None, **context
    ) -> dict[str, Any]:
        return {"tags": nous_portal_tags()}

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        supports_reasoning: bool = False,
        **context,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Nous: passes full reasoning_config, but OMITS when disabled."""
        extra_body = {}
        if supports_reasoning:
            if reasoning_config is not None:
                rc = dict(reasoning_config)
                if rc.get("enabled") is False:
                    pass  # Nous omits reasoning when disabled
                else:
                    extra_body["reasoning"] = rc
            else:
                extra_body["reasoning"] = {"enabled": True, "effort": "medium"}
        return extra_body, {}


nous = NousProfile(
    name="nous",
    aliases=("nous-portal", "nousresearch"),
    env_vars=("NOUS_API_KEY",),
    display_name="Nous Research",
    description="Nous Portal — curated multi-provider agentic models",
    signup_url="https://nousresearch.com/",
    fallback_models=(
        "anthropic/claude-opus-4.8",
        "anthropic/claude-sonnet-4.6",
        "anthropic/claude-haiku-4.5",
        "openai/gpt-5.5",
        "openai/gpt-5.5-pro",
        "openai/gpt-5.4-mini",
        "google/gemini-3-pro-preview",
        "google/gemini-3.1-pro-preview",
        "google/gemini-3.5-flash",
        "x-ai/grok-4.3",
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-v4-flash",
        "qwen/qwen3.7-max",
        "qwen/qwen3.6-35b-a3b",
        "moonshotai/kimi-k2.6",
        "minimax/minimax-m3",
        "z-ai/glm-5.1",
        "xiaomi/mimo-v2.5-pro",
        "tencent/hy3-preview",
        "stepfun/step-3.7-flash",
        "nvidia/nemotron-3-super-120b-a12b",
    ),
    base_url="https://inference-api.nousresearch.com/v1",
    auth_type="oauth_device_code",
)

register_provider(nous)
