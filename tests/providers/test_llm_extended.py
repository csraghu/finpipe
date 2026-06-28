import httpx
import pytest
import respx
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.core.registry import BuildContext
from finpipe.providers.gemini import GeminiAdapter
from finpipe.providers.groq import build_groq
from finpipe.providers.nvidia import NvidiaAdapter


@pytest.mark.asyncio
async def test_gemini_cache_and_failures(config):
    adapter = GeminiAdapter(config)
    cached = {
        "model_name": "gemini-3.1-flash-lite",
        "content": "cached",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "raw_response": {},
    }
    adapter._cache.set(f"gemini_gemini-3.1-flash-lite_{hash('prompt')}", cached, 60)
    assert (await adapter.generate_response("prompt")).content == "cached"

    with respx.mock:
        respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(FinpipeProviderDownError):
            await adapter.generate_response("fresh")


@pytest.mark.asyncio
async def test_nvidia_cache_and_failures(config):
    adapter = NvidiaAdapter(config)
    cached = {
        "model_name": "meta/llama-3.1-70b-instruct",
        "content": "cached",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "raw_response": {},
    }
    adapter._cache.set(f"nvidia_meta/llama-3.1-70b-instruct_{hash('prompt')}", cached, 60)
    assert (await adapter.generate_response("prompt")).content == "cached"

    with respx.mock:
        respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"candidates": []})
        )
        with pytest.raises(FinpipeProviderDownError):
            await adapter.generate_response("empty")


def test_build_groq_factory(config):
    assert build_groq(BuildContext(config=config))
