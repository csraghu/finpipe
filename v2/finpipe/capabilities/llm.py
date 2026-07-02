"""Typed LLM capability service with primary/fallback routing.

v1 configured ``routing.llm_primary/llm_fallback`` but never implemented the
composite — this closes that gap (review §4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.models import LLMResponse
from .equity import build_route
from .policy import call_with_fallback

if TYPE_CHECKING:
    from ..core.config import FinpipeConfig
    from ..providers.wiring import AdapterPool


class LlmService:
    def __init__(self, pool: AdapterPool, config: FinpipeConfig) -> None:
        self._pool = pool
        self._route = build_route(config, "llm_primary", "llm_fallback")

    async def generate_response(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> LLMResponse:
        attempts = []
        for name in self._route:
            adapter = self._pool.get_if_enabled(name)
            if adapter is None:
                continue
            attempts.append(
                (name, lambda a=adapter: a.generate_response(prompt, model=model, **kwargs))
            )
        return await call_with_fallback("llm.generate_response", attempts)
