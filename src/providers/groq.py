import logging
from typing import Any

from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.core.interfaces import ILLMProvider, IProviderDescribe
from finpipe.core.models import LLMResponse
from finpipe.core.registry import BuildContext, register_provider
from finpipe.network.cache import create_cache_backend
from finpipe.network.resilience import create_resilient_http_client
from finpipe.providers.descriptor import provider_descriptor

logger = logging.getLogger(__name__)


class GroqAdapter(ILLMProvider, IProviderDescribe):
    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._provider_config = config.providers.groq
        self._provider_config.ensure_configured()
        self._api_key = self._provider_config.api_key
        self._client = create_resilient_http_client(
            "groq", self._provider_config.rate_limits, cache_config=config.cache
        )
        self._cache = create_cache_backend(config.cache)
        self._base_url = "https://api.groq.com/openai/v1/chat/completions"

    async def close(self) -> None:
        await self._client.close()

    async def _remote_models(self) -> list[str]:
        if not self._api_key:
            return []
        response = await self._client.request(
            "GET",
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        data = response.json()
        return [model["id"] for model in data.get("data", []) if model.get("id")]

    async def describe(self) -> dict[str, Any]:
        models = await self._remote_models()
        cfg = self._provider_config
        return provider_descriptor(
            provider_id="groq",
            capability="llm",
            provider_config=cfg,
            configured=bool(self._api_key),
            details={
                "default_model": cfg.model,
                "temperature": cfg.temperature,
                "max_tokens": cfg.max_tokens,
                "use_dynamic_model": cfg.use_dynamic_model,
                "models": models,
            },
        )

    async def generate_response(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> LLMResponse:
        model_name = model or self._provider_config.model
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": kwargs.pop("temperature", self._provider_config.temperature),
            "max_tokens": kwargs.pop("max_tokens", self._provider_config.max_tokens),
            **kwargs,
        }

        cache_key = f"groq_{model_name}_{hash(prompt)}"
        cached_data = self._cache.get(cache_key)
        if cached_data is not None:
            return LLMResponse(**cached_data)

        try:
            response = await self._client.request(
                "POST", self._base_url, headers=headers, json=payload
            )
            data = response.json()
        except Exception as exc:
            logger.error("Groq API request failed: %s", exc)
            raise FinpipeProviderDownError("Failed to communicate with Groq API") from exc

        choices = data.get("choices", [])
        if not choices:
            raise FinpipeProviderDownError("Groq API returned an empty response")

        content = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        response_obj = LLMResponse(
            model_name=model_name,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            content=content,
            raw_response=data,
        )
        self._cache.set(
            cache_key, response_obj.model_dump(), self._provider_config.ttls.generate_response_sec
        )
        return response_obj


@register_provider("groq", category="llm")
def build_groq(ctx: BuildContext) -> GroqAdapter:
    return GroqAdapter(ctx.config)
