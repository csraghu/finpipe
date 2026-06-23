import httpx
import pytest
import respx
from finpipe.providers.gemini import GeminiAdapter
from finpipe.providers.groq import GroqAdapter


@pytest.mark.asyncio
async def test_groq_adapter(config):
    adapter = GroqAdapter(config)
    json_mock = {
        "choices": [{"message": {"content": "Hello world"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    }

    with respx.mock:
        respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        resp = await adapter.generate_response("Say hello")
        assert resp.content == "Hello world"
        assert resp.model_name == "llama3-8b-8192"
        assert resp.prompt_tokens == 10


@pytest.mark.asyncio
async def test_groq_adapter_uses_configured_model(config):
    from finpipe.core.config import FinpipeConfig

    custom = FinpipeConfig.from_dict(
        {"providers": {"groq": {"model": "llama-3.3-70b-versatile"}}}
    )
    adapter = GroqAdapter(custom)
    json_mock = {
        "choices": [{"message": {"content": "Custom model"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    with respx.mock:
        route = respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        resp = await adapter.generate_response("Say hello")
        assert resp.content == "Custom model"
        assert route.calls.last.request.content is not None
        assert b"llama-3.3-70b-versatile" in route.calls.last.request.content


@pytest.mark.asyncio
async def test_gemini_adapter(config):
    adapter = GeminiAdapter(config)
    json_mock = {
        "candidates": [{"content": {"parts": [{"text": "Hello Gemini"}]}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
    }

    with respx.mock:
        respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        resp = await adapter.generate_response("Say hello")
        assert resp.content == "Hello Gemini"
        assert resp.model_name == "gemini-1.5-flash"
        assert resp.prompt_tokens == 5


@pytest.mark.asyncio
async def test_gemini_adapter_uses_configured_model(config):
    from finpipe.core.config import FinpipeConfig

    custom = FinpipeConfig.from_dict(
        {"providers": {"gemini": {"model": "gemini-2.0-flash"}}}
    )
    adapter = GeminiAdapter(custom)
    json_mock = {
        "candidates": [{"content": {"parts": [{"text": "Gemini 2"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
    }

    with respx.mock:
        route = respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        resp = await adapter.generate_response("Say hello")
        assert resp.content == "Gemini 2"
        assert resp.model_name == "gemini-2.0-flash"
        assert "gemini-2.0-flash" in str(route.calls.last.request.url)
