import json


def test_image_generation_tries_configured_fallback_models(monkeypatch):
    import tools.image_generation_tool as image_tool

    monkeypatch.setattr(
        image_tool,
        "load_tool_fallbacks",
        lambda section: [
            {"provider": "fal", "model": "bad-model"},
            {"provider": "fal", "model": "good-model"},
        ] if section == "image_gen" else [],
    )
    calls = []

    def fake_image_generate_tool(**kwargs):
        calls.append(kwargs.get("model"))
        if kwargs.get("model") == "good-model":
            return json.dumps({"success": True, "image": "https://example.test/image.png"})
        return json.dumps({"success": False, "image": None, "error": "boom"})

    monkeypatch.setattr(image_tool, "image_generate_tool", fake_image_generate_tool)

    result = json.loads(image_tool._handle_image_generate({"prompt": "x", "aspect_ratio": "square"}))

    assert result["success"] is True
    assert result["model"] == "good-model"
    assert result["fallback_used"] is True
    assert calls == ["bad-model", "good-model"]


def test_tts_tries_configured_provider_fallbacks(monkeypatch):
    import tools.tts_tool as tts_tool

    monkeypatch.setattr(
        tts_tool,
        "load_tool_fallbacks",
        lambda section: [
            {"provider": "openai", "use_gateway": True},
            {"provider": "edge"},
        ] if section == "tts" else [],
    )
    monkeypatch.setattr(tts_tool, "_load_tts_config", lambda: {"provider": "openai"})
    calls = []

    def fake_once(text, output_path=None):
        provider = tts_tool._load_tts_config()["provider"]
        calls.append(provider)
        if provider == "edge":
            return json.dumps({"success": True, "provider": "edge", "file_path": "/tmp/a.mp3"})
        return json.dumps({"success": False, "error": "no key"})

    monkeypatch.setattr(tts_tool, "_text_to_speech_once", fake_once)

    result = json.loads(tts_tool.text_to_speech_tool("hello"))

    assert result["success"] is True
    assert result["provider"] == "edge"
    assert result["fallback_used"] is True
    assert calls == ["openai", "edge"]


def test_web_fallback_backends_prefers_configured_order(monkeypatch):
    import tools.web_tools as web_tools

    monkeypatch.setattr(
        web_tools,
        "load_tool_fallbacks",
        lambda section: [
            {"backend": "firecrawl"},
            {"backend": "tinyfish"},
        ] if section == "web" else [],
    )
    monkeypatch.setattr(web_tools, "_get_capability_backend", lambda capability: "ddgs")

    assert web_tools._web_fallback_backends("search") == ["firecrawl", "tinyfish", "ddgs"]
