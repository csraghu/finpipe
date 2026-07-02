"""LLM adapter tests: shared generate flow, digest cache keys, cache-before-compression."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from conftest import FakeExecutor, FakeResponse, make_runtime
from finpipe.core.config import GeminiConfig, GroqConfig
from finpipe.core.errors import FinpipeConfigError, FinpipeProviderDownError
from finpipe.providers.llm.gemini import GeminiAdapter
from finpipe.providers.llm.openai_compat import GroqAdapter

_GROQ_PAYLOAD = {
    "choices": [{"message": {"content": "OK"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
}
_GEMINI_PAYLOAD = {
    "candidates": [{"content": {"parts": [{"text": "OK"}]}}],
    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
}


def _groq(executor: FakeExecutor, *, api_key: str | None = "gk") -> GroqAdapter:
    config = GroqConfig(api_key=SecretStr(api_key) if api_key else None)
    return GroqAdapter(make_runtime(config, executor, provider_key="groq"))


async def test_missing_key_raises_on_first_use():
    adapter = _groq(FakeExecutor(), api_key=None)
    with pytest.raises(FinpipeConfigError):
        await adapter.generate_response("hello")


async def test_generate_parses_and_reconciles_tokens():
    executor = FakeExecutor([FakeResponse(200, json_data=_GROQ_PAYLOAD)])
    adapter = _groq(executor)
    result = await adapter.generate_response("hello world")
    assert result.content == "OK"
    assert result.prompt_tokens == 5 and result.completion_tokens == 3
    assert executor.reconciled and executor.reconciled[0][1] == 8

    method, url, kwargs = executor.calls[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer gk"


async def test_second_identical_prompt_is_served_from_cache():
    executor = FakeExecutor([FakeResponse(200, json_data=_GROQ_PAYLOAD)])
    adapter = _groq(executor)
    first = await adapter.generate_response("same prompt")
    second = await adapter.generate_response("same prompt")
    assert len(executor.calls) == 1  # digest-keyed cache hit; no second HTTP call
    assert second.content == first.content


async def test_empty_choices_is_provider_down():
    executor = FakeExecutor([FakeResponse(200, json_data={"choices": []})])
    adapter = _groq(executor)
    with pytest.raises(FinpipeProviderDownError):
        await adapter.generate_response("hello")


async def test_gemini_key_travels_in_header_not_url():
    executor = FakeExecutor([FakeResponse(200, json_data=_GEMINI_PAYLOAD)])
    adapter = GeminiAdapter(
        make_runtime(GeminiConfig(api_key=SecretStr("gsecret")), executor, provider_key="gemini")
    )
    result = await adapter.generate_response("hello")
    assert result.content == "OK"
    method, url, kwargs = executor.calls[0]
    assert "gsecret" not in url  # v1 put ?key=... in the URL (leaked into logs)
    assert kwargs["headers"]["x-goog-api-key"] == "gsecret"
