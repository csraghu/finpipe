import logging
from typing import Any

from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeProviderDownError, FinpipeRateLimitExceededError
from finpipe.core.interfaces import ILLMProvider, IProviderDescribe
from finpipe.core.models import LLMResponse
from finpipe.core.registry import BuildContext, register_provider
from finpipe.network.cache_manager import resolve_cache_backend
from finpipe.network.limiter import estimate_llm_token_usage
from finpipe.network.resilience import create_resilient_http_client
from finpipe.providers.descriptor import provider_descriptor
from finpipe.providers.llm_base import LlmProviderBase

logger = logging.getLogger(__name__)


class GeminiAdapter(LlmProviderBase, ILLMProvider, IProviderDescribe):
    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._provider_config = config.providers.gemini
        self._provider_config.ensure_configured()
        self._api_key = self._provider_config.api_key
        self._client = create_resilient_http_client(
            "gemini", self._provider_config.rate_limits, cache_config=config.cache
        )
        self._cache = resolve_cache_backend(config.cache)
        self._base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    async def close(self) -> None:
        await self._client.close()
        if hasattr(self, "_compression_client") and self._compression_client is not None:
            await self._compression_client.close()

    async def _remote_models(self) -> list[str]:
        if not self._api_key:
            return []
        url = f"{self._base_url}?key={self._api_key}"
        response = await self._client.request("GET", url)
        payload = response.json()
        return [
            name.split("/")[-1]
            for model in payload.get("models", [])
            if (name := model.get("name"))
        ]

    async def describe(self) -> dict[str, Any]:
        models = await self._remote_models()
        cfg = self._provider_config
        return provider_descriptor(
            provider_id="gemini",
            capability="llm",
            provider_config=cfg,
            configured=bool(self._api_key),
            details={
                "default_model": cfg.model,
                "temperature": cfg.temperature,
                "max_tokens": cfg.max_tokens,
                "models": models,
            },
        )

    async def generate_response(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> LLMResponse:
        prompt = await self.prepare_prompt(prompt)
        model_name = model or self._provider_config.model
        generation_config = dict(kwargs.get("generationConfig") or {})
        if "temperature" not in generation_config:
            generation_config["temperature"] = self._provider_config.temperature
        if "maxOutputTokens" not in generation_config:
            generation_config["maxOutputTokens"] = self._provider_config.max_tokens
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        cache_key = f"gemini_{model_name}_{hash(prompt)}"
        cached_data = self._cache.get(cache_key)
        if cached_data is not None:
            return LLMResponse(**cached_data)

        url = f"{self._base_url}/{model_name}:generateContent?key={self._api_key}"
        estimated = estimate_llm_token_usage(prompt, generation_config["maxOutputTokens"])
        try:
            response = await self._client.request(
                "POST",
                url,
                headers={"Content-Type": "application/json"},
                json=payload,
                token_estimate=estimated,
            )
            data = response.json()
        except (FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as exc:
            logger.error("Gemini API request failed: %s", exc)
            raise FinpipeProviderDownError(f"Failed to communicate with Gemini API: {exc}") from exc

        candidates = data.get("candidates", [])
        if not candidates:
            raise FinpipeProviderDownError("Gemini API returned an empty response")

        content_part = candidates[0].get("content", {}).get("parts", [{}])[0]
        content_text = content_part.get("text", "")
        usage = data.get("usageMetadata", {})
        actual_tokens = usage.get("totalTokenCount")
        if actual_tokens is None:
            prompt_tokens = usage.get("promptTokenCount")
            completion_tokens = usage.get("candidatesTokenCount")
            if prompt_tokens is not None and completion_tokens is not None:
                actual_tokens = prompt_tokens + completion_tokens
        if actual_tokens is not None:
            await self._client.reconcile_token_usage(estimated, actual_tokens)
        response_obj = LLMResponse(
            model_name=model_name,
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            content=content_text,
            raw_response=data,
        )
        self._cache.set(
            cache_key, response_obj.model_dump(), self._provider_config.ttls.generate_response_sec
        )
        return response_obj


@register_provider("gemini", category="llm")
def build_gemini(ctx: BuildContext) -> GeminiAdapter:
    return GeminiAdapter(ctx.config)
