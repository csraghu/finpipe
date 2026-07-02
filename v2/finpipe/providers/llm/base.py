"""Shared LLM adapter machinery: prompt preparation + cached generation flow.

v2 fixes vs v1:
- prompt-cache keys are sha256 digests via ``NamespacedCache.key`` (v1 used the
  salted builtin ``hash()`` — unstable across processes, collision-prone)
- cache is checked BEFORE prompt compression (v1 compressed first, wasting a
  remote call on every cache hit)
- one shared ``generate`` flow; vendor adapters implement only payload/parse
  (v1 duplicated ~85% of Groq/Gemini/NVIDIA)
"""

from __future__ import annotations

import logging
from typing import Any

from ...core.config import LlmPromptConfig, LlmProviderConfig
from ...core.errors import FinpipeConfigError, FinpipeError, FinpipeProviderDownError
from ...runtime.ratelimit import estimate_llm_token_usage
from ...runtime.resilience import RequestExecutor
from ..base import ProviderAdapter, ProviderRuntime
from .sanitize import sanitize_llm_text

logger = logging.getLogger(__name__)


class LlmAdapterBase(ProviderAdapter):
    provider_key: str = "llm"

    def __init__(self, runtime: ProviderRuntime) -> None:
        super().__init__(runtime)
        self._config: LlmProviderConfig = runtime.config
        self._prompt_config: LlmPromptConfig | None = runtime.llm_prompt
        self._compression_executor: RequestExecutor | None = None

    # -- credential gate ------------------------------------------------------------
    def _ensure_configured(self) -> None:
        if self._config.api_key is None:
            raise FinpipeConfigError(
                f"{self.provider_key} requires its API key env var; "
                f"set it or disable providers.{self.provider_key}"
            )
        super()._ensure_configured()

    def _api_key(self) -> str:
        assert self._config.api_key is not None
        return self._config.api_key.get_secret_value()

    # -- prompt preparation ---------------------------------------------------------
    async def _prepare_prompt(self, text: str, symbol: str | None) -> str:
        prepared = sanitize_llm_text(text)
        compression = self._prompt_config.compression if self._prompt_config else None
        if (
            compression is None
            or not compression.enabled
            or len(prepared) < compression.min_chars
            or not compression.endpoint_url
        ):
            return prepared
        try:
            return await self._compress(prepared, compression, symbol)
        except FinpipeError as exc:
            logger.warning("Prompt compression failed; using sanitized text: %s", exc)
            return prepared

    async def _compress(self, text: str, compression: Any, symbol: str | None) -> str:
        if self._compression_executor is None:
            assert self._rt.executor_factory is not None
            from ...core.config import HttpConfig

            self._compression_executor = self._rt.executor_factory(
                "llm_compression", compression.rate_limits, HttpConfig()
            )
        payload: dict[str, Any] = {
            "text": text,
            "target_ratio": compression.target_ratio,
            "model_name": compression.model_name,
        }
        if symbol:
            payload["symbol"] = symbol
        response = await self._compression_executor.request(
            "POST", compression.endpoint_url, json=payload
        )
        data = response.json()
        compressed = data.get("compressed_prompt")
        return compressed if isinstance(compressed, str) and compressed.strip() else text

    # -- shared generation flow --------------------------------------------------------
    async def generate_response(self, prompt: str, model: str | None = None, **kwargs: Any) -> Any:
        from ...core.models import LLMResponse

        symbol = kwargs.pop("symbol", None)
        model_name = model or self._config.model
        max_tokens = int(kwargs.get("max_tokens", self._config.max_tokens))

        if not self._validated:
            self._ensure_configured()

        # Cache BEFORE compression: identical raw prompts hit without remote calls.
        cache_key = self._rt.cache.key("generate_response", model_name, prompt, max_tokens)
        cached = await self._rt.cache.get(cache_key)
        if cached is not None:
            return LLMResponse.model_validate(cached)

        prepared = await self._prepare_prompt(prompt, symbol)
        estimated = estimate_llm_token_usage(prepared, max_tokens)
        response = await self._rt.executor.request(
            self._http_method(),
            self._endpoint(model_name),
            headers=self._headers(),
            json=self._payload(prepared, model_name, **kwargs),
            token_estimate=estimated,
        )
        data = response.json()
        result = self._parse_response(data, model_name)
        if result.prompt_tokens is not None and result.completion_tokens is not None:
            await self._rt.executor.reconcile_token_usage(
                estimated, result.prompt_tokens + result.completion_tokens
            )
        await self._rt.cache.set(
            cache_key, result.model_dump(), self._config.ttls.generate_response_sec
        )
        return result

    # -- vendor hooks -------------------------------------------------------------------
    def _http_method(self) -> str:
        return "POST"

    def _endpoint(self, model_name: str) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _payload(self, prompt: str, model_name: str, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def _parse_response(self, data: dict[str, Any], model_name: str) -> Any:
        raise NotImplementedError

    # -- shared helpers -------------------------------------------------------------------
    @staticmethod
    def _require_content(candidates: Any, provider_key: str) -> None:
        if not candidates:
            raise FinpipeProviderDownError(f"{provider_key} returned an empty response")

    async def close(self) -> None:
        if self._compression_executor is not None:
            await self._compression_executor.close()
        await super().close()
