from __future__ import annotations

import httpx
import pytest
import respx
from finpipe.core.config import GroqConfig
from finpipe.providers.descriptor import provider_descriptor, redact_secrets
from finpipe.providers.gemini import GeminiAdapter
from finpipe.providers.groq import GroqAdapter
from finpipe.providers.nvidia import NvidiaAdapter


def test_redact_secrets_masks_api_keys():
    data = {"api_key": "secret", "model": "test", "nested": {"access_key_id": "abc"}}
    redacted = redact_secrets(data)
    assert redacted["api_key"] == "<configured>"
    assert redacted["model"] == "test"
    assert redacted["nested"]["access_key_id"] == "<configured>"


def test_provider_descriptor_includes_settings():
    cfg = GroqConfig(api_key="secret", enabled=True)
    payload = provider_descriptor(
        provider_id="groq",
        capability="llm",
        provider_config=cfg,
        configured=True,
        details={"models": ["a"]},
    )
    assert payload["provider_id"] == "groq"
    assert payload["settings"]["api_key"] == "<configured>"
    assert payload["details"]["models"] == ["a"]


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
        assert resp.model_name == "meta-llama/llama-4-scout-17b-16e-instruct"
        assert resp.prompt_tokens == 10


@pytest.mark.asyncio
async def test_groq_adapter_uses_configured_model(config):
    from finpipe.core.config import FinpipeConfig

    custom = FinpipeConfig.from_dict({"providers": {"groq": {"model": "llama-3.3-70b-versatile"}}})
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
async def test_groq_describe_includes_models_and_limits(config):
    adapter = GroqAdapter(config)

    with respx.mock:
        respx.get("https://api.groq.com/openai/v1/models").mock(
            return_value=httpx.Response(
                200, json={"data": [{"id": "meta-llama/llama-4-scout-17b-16e-instruct"}]}
            )
        )
        info = await adapter.describe()

    assert info["provider_id"] == "groq"
    assert info["capability"] == "llm"
    assert info["details"]["default_model"]
    assert info["details"]["models"] == ["meta-llama/llama-4-scout-17b-16e-instruct"]
    assert info["settings"]["rate_limits"]["max_requests_per_minute"] == 30
    assert info["settings"]["api_key"] == "<configured>"


@pytest.mark.asyncio
async def test_gemini_adapter_sanitizes_prompt_before_request(config):
    adapter = GeminiAdapter(config)
    json_mock = {
        "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 1},
    }
    raw_prompt = "<p>Hello &#128640;</p> [link](https://example.com/x?utm_source=spam)"

    with respx.mock:
        route = respx.post(url__startswith="https://generativelanguage.googleapis.com").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        await adapter.generate_response(raw_prompt)
        body = route.calls.last.request.content.decode()
        assert "&#128640;" not in body
        assert "<p>" not in body
        assert "utm_source" not in body
        assert "Hello" in body
        assert "https://example.com/x" in body


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
        assert resp.model_name == "gemini-3.1-flash-lite"
        assert resp.prompt_tokens == 5


@pytest.mark.asyncio
async def test_gemini_adapter_uses_configured_model(config):
    from finpipe.core.config import FinpipeConfig

    custom = FinpipeConfig.from_dict({"providers": {"gemini": {"model": "gemini-2.0-flash"}}})
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


@pytest.mark.asyncio
async def test_gemini_describe_includes_models(config):
    adapter = GeminiAdapter(config)

    with respx.mock:
        respx.get(url__startswith="https://generativelanguage.googleapis.com").mock(
            return_value=httpx.Response(
                200,
                json={"models": [{"name": "models/gemini-3.1-flash-lite"}]},
            )
        )
        info = await adapter.describe()

    assert info["provider_id"] == "gemini"
    assert info["details"]["models"] == ["gemini-3.1-flash-lite"]


@pytest.mark.asyncio
async def test_nvidia_adapter(config):
    adapter = NvidiaAdapter(config)
    json_mock = {
        "choices": [{"message": {"content": "Hello NVIDIA"}}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 3},
    }

    with respx.mock:
        respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        resp = await adapter.generate_response("Say hello")
        assert resp.content == "Hello NVIDIA"
        assert resp.model_name == "meta/llama-3.1-70b-instruct"
        assert resp.prompt_tokens == 8


@pytest.mark.asyncio
async def test_nvidia_adapter_uses_configured_model(config):
    from finpipe.core.config import FinpipeConfig

    custom = FinpipeConfig.from_dict(
        {"providers": {"nvidia": {"model": "meta/llama-3.3-70b-instruct"}}}
    )
    adapter = NvidiaAdapter(custom)
    json_mock = {
        "choices": [{"message": {"content": "NVIDIA custom"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    with respx.mock:
        route = respx.post("https://integrate.api.nvidia.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        resp = await adapter.generate_response("Say hello")
        assert resp.content == "NVIDIA custom"
        assert resp.model_name == "meta/llama-3.3-70b-instruct"
        assert route.calls.last.request.content is not None
        assert b"meta/llama-3.3-70b-instruct" in route.calls.last.request.content


@pytest.mark.asyncio
async def test_nvidia_describe_includes_models(config):
    adapter = NvidiaAdapter(config)

    with respx.mock:
        respx.get("https://integrate.api.nvidia.com/v1/models").mock(
            return_value=httpx.Response(
                200,
                json={"data": [{"id": "meta/llama-3.1-70b-instruct"}]},
            )
        )
        info = await adapter.describe()

    assert info["provider_id"] == "nvidia"
    assert info["details"]["models"] == ["meta/llama-3.1-70b-instruct"]
