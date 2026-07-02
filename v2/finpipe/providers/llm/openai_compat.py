"""OpenAI-compatible chat adapters: Groq and NVIDIA NIM (one class, two manifests).

v1 shipped ~100 near-identical lines per vendor; here the difference is a base
URL and a manifest entry.
"""

from __future__ import annotations

from typing import Any

from ...core.models import LLMResponse
from ..base import ProviderRuntime
from ..manifest import provider
from .base import LlmAdapterBase


class OpenAICompatAdapter(LlmAdapterBase):
    base_url: str = ""

    async def describe(self) -> dict[str, Any]:
        from ...observe.describe import provider_descriptor

        return provider_descriptor(
            self.provider_key, "llm", self._config,
            configured=self._config.api_key is not None,
            details={
                "api_base_url": self.base_url,
                "default_model": self._config.model,
                "temperature": self._config.temperature,
                "max_tokens": self._config.max_tokens,
            },
        )

    def _endpoint(self, model_name: str) -> str:
        return f"{self.base_url}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key()}", "Content-Type": "application/json"}

    def _payload(self, prompt: str, model_name: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.pop("temperature", self._config.temperature),
            "max_tokens": kwargs.pop("max_tokens", self._config.max_tokens),
            **kwargs,
        }

    def _parse_response(self, data: dict[str, Any], model_name: str) -> LLMResponse:
        choices = data.get("choices", [])
        self._require_content(choices, self.provider_key)
        usage = data.get("usage", {})
        return LLMResponse(
            model_name=model_name,
            content=choices[0].get("message", {}).get("content", ""),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            raw_response=data,
        )

    async def list_models(self) -> list[str]:
        if self._config.api_key is None:
            return []
        response = await self._rt.executor.request(
            "GET", f"{self.base_url}/models", headers=self._headers()
        )
        return [m["id"] for m in response.json().get("data", []) if m.get("id")]


class GroqAdapter(OpenAICompatAdapter):
    provider_key = "groq"
    base_url = "https://api.groq.com/openai/v1"


class NvidiaAdapter(OpenAICompatAdapter):
    provider_key = "nvidia"
    base_url = "https://integrate.api.nvidia.com/v1"


@provider(
    "groq",
    capability="llm",
    config_attr="groq",
    label="Groq",
    description="Groq chat completions (OpenAI-compatible)",
    secrets=("GROQ_API_KEY",),
    probe="llm.groq",
)
def build_groq(runtime: ProviderRuntime) -> GroqAdapter:
    return GroqAdapter(runtime)


@provider(
    "nvidia",
    capability="llm",
    config_attr="nvidia",
    label="NVIDIA NIM",
    description="NVIDIA NIM chat completions (OpenAI-compatible)",
    secrets=("NVIDIA_API_KEY",),
    probe="llm.nvidia",
)
def build_nvidia(runtime: ProviderRuntime) -> NvidiaAdapter:
    return NvidiaAdapter(runtime)
