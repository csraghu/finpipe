"""Gemini adapter.

v2 fix vs v1 (review §3): the API key travels in the ``x-goog-api-key`` HEADER,
never in the URL — v1 put it in the query string, which leaked into circuit
breaker log/exception text.
"""

from __future__ import annotations

from typing import Any

from ...core.models import LLMResponse
from ..base import ProviderRuntime
from ..manifest import provider
from .base import LlmAdapterBase

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiAdapter(LlmAdapterBase):
    provider_key = "gemini"

    async def describe(self) -> dict[str, Any]:
        from ...observe.describe import provider_descriptor

        return provider_descriptor(
            "gemini", "llm", self._config,
            configured=self._config.api_key is not None,
            details={
                "api_base_url": _BASE_URL,
                "default_model": self._config.model,
                "temperature": self._config.temperature,
                "max_tokens": self._config.max_tokens,
            },
        )

    def _endpoint(self, model_name: str) -> str:
        return f"{_BASE_URL}/{model_name}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key(), "Content-Type": "application/json"}

    def _payload(self, prompt: str, model_name: str, **kwargs: Any) -> dict[str, Any]:
        generation_config = dict(kwargs.get("generationConfig") or {})
        generation_config.setdefault("temperature", kwargs.pop("temperature", self._config.temperature))
        generation_config.setdefault("maxOutputTokens", kwargs.pop("max_tokens", self._config.max_tokens))
        return {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": generation_config}

    def _parse_response(self, data: dict[str, Any], model_name: str) -> LLMResponse:
        candidates = data.get("candidates", [])
        self._require_content(candidates, "gemini")
        part = candidates[0].get("content", {}).get("parts", [{}])[0]
        usage = data.get("usageMetadata", {})
        return LLMResponse(
            model_name=model_name,
            content=part.get("text", ""),
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            raw_response=data,
        )

    async def list_models(self) -> list[str]:
        if self._config.api_key is None:
            return []
        response = await self._rt.executor.request("GET", _BASE_URL, headers=self._headers())
        return [
            name.split("/")[-1]
            for model in response.json().get("models", [])
            if (name := model.get("name"))
        ]


@provider(
    "gemini",
    capability="llm",
    config_attr="gemini",
    label="Google Gemini",
    description="Gemini generateContent completions",
    secrets=("GEMINI_API_KEY",),
    probe="llm.gemini",
)
def build_gemini(runtime: ProviderRuntime) -> GeminiAdapter:
    return GeminiAdapter(runtime)
