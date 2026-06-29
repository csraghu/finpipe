"""Shared base for LLM provider adapters (prompt prep is provider-agnostic)."""

from __future__ import annotations

from typing import Any

from finpipe.core.config import FinpipeConfig
from finpipe.core.llm_prompt import prepare_llm_prompt


class LlmProviderBase:
    """Mixin: sanitize + compress prompts before any LLM HTTP call."""

    _config: FinpipeConfig
    _compression_client: Any = None

    async def prepare_prompt(self, text: str) -> str:
        """Normalize and optionally compress prompt text for LLM inference."""
        if self._compression_client is None:
            from finpipe.network.resilience import create_resilient_http_client
            self._compression_client = create_resilient_http_client(
                "huggingface_compression",
                self._config.llm_prompt.compression.rate_limits,
            )
        return await prepare_llm_prompt(text, self._config.llm_prompt.compression, client=self._compression_client)
