from agent.chat_completion_helpers import _fallback_endpoint_uses_anthropic_messages


def test_kimi_coding_endpoint_uses_anthropic_messages_transport():
    assert _fallback_endpoint_uses_anthropic_messages(
        "kimi-coding",
        "https://api.kimi.com/coding",
    )


def test_kimi_coding_endpoint_accepts_trailing_v1_suffix():
    assert _fallback_endpoint_uses_anthropic_messages(
        "kimi-coding",
        "https://api.kimi.com/coding/v1/",
    )


def test_legacy_moonshot_endpoint_stays_chat_completions():
    assert not _fallback_endpoint_uses_anthropic_messages(
        "kimi-coding",
        "https://api.moonshot.ai/v1",
    )


def test_anthropic_provider_and_suffix_still_use_anthropic_messages():
    assert _fallback_endpoint_uses_anthropic_messages(
        "anthropic",
        "https://api.anthropic.com",
    )
    assert _fallback_endpoint_uses_anthropic_messages(
        "custom",
        "https://api.anthropic.com",
    )
    assert _fallback_endpoint_uses_anthropic_messages(
        "custom",
        "https://example.invalid/anthropic",
    )
    assert _fallback_endpoint_uses_anthropic_messages(
        "custom",
        "https://example.invalid/anthropic/v1",
    )


def test_openai_compatible_endpoint_stays_chat_completions():
    assert not _fallback_endpoint_uses_anthropic_messages(
        "custom",
        "https://openai-compatible.example.invalid/v1",
    )
