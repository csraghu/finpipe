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
        assert resp.prompt_tokens == 10


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
        assert resp.prompt_tokens == 5
