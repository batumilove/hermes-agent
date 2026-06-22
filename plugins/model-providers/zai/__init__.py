"""ZAI / GLM provider profile."""

from providers import register_provider
from providers.base import ProviderProfile


class ZAIProviderProfile(ProviderProfile):
    def build_api_kwargs_extras(self, *, reasoning_config=None, **context):
        extra_body: dict[str, object] = {}
        if isinstance(reasoning_config, dict):
            enabled = reasoning_config.get("enabled")
            if enabled is False:
                extra_body["thinking"] = {"type": "disabled"}
        return extra_body, {}


zai = ZAIProviderProfile(
    name="zai",
    aliases=("glm", "z-ai", "z.ai", "zhipu"),
    env_vars=("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"),
    display_name="Z.AI (GLM)",
    description="Z.AI / GLM — Zhipu AI models",
    signup_url="https://z.ai/",
    fallback_models=(
        "glm-5.2",
        "glm-5.1",
        "glm-5",
        "glm-5-turbo",
        "glm-4.7-flash",
        "glm-4.7-flashx",
        "glm-4.7",
        "glm-4.5-air",
        "glm-4.5",
        "glm-4.5-flash",
    ),
    base_url="https://api.z.ai/api/paas/v4",
    default_aux_model="glm-4.5-flash",
)

register_provider(zai)
