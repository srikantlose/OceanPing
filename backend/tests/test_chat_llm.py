import httpx

from app.modules.chat import llm


def test_anthropic_adapter_skips_without_api_key(monkeypatch):
    settings = llm.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    result = llm.AnthropicAdapter().complete("system prompt", "hello")
    assert result is None


def test_anthropic_adapter_parses_text_content(monkeypatch):
    settings = llm.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")

    def _fake_post(url, headers=None, json=None, timeout=None):
        assert url == llm.ANTHROPIC_API_URL
        assert headers["x-api-key"] == "sk-test"
        assert json["messages"][0]["content"] == "hello"
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "General coastal safety info."}]},
            request=request,
        )

    monkeypatch.setattr(httpx, "post", _fake_post)
    result = llm.AnthropicAdapter().complete("system prompt", "hello")
    assert result == "General coastal safety info."


def test_anthropic_adapter_returns_none_on_http_error(monkeypatch):
    settings = llm.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")

    def _fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(500, json={"error": "boom"}, request=request)

    monkeypatch.setattr(httpx, "post", _fake_post)
    result = llm.AnthropicAdapter().complete("system prompt", "hello")
    assert result is None


def test_anthropic_adapter_returns_none_on_empty_text(monkeypatch):
    settings = llm.get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")

    def _fake_post(url, headers=None, json=None, timeout=None):
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"content": []}, request=request)

    monkeypatch.setattr(httpx, "post", _fake_post)
    result = llm.AnthropicAdapter().complete("system prompt", "hello")
    assert result is None


def test_get_adapter_returns_anthropic_adapter():
    assert isinstance(llm.get_adapter(), llm.AnthropicAdapter)
