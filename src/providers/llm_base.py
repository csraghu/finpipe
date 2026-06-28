"""Shared base for LLM provider adapters (prompt prep is provider-agnostic)."""

from __future__ import annotations

from finpipe.core.config import FinpipeConfig
from finpipe.core.llm_prompt import prepare_llm_prompt


class LlmProviderBase:
    """Mixin: sanitize + compress prompts before any LLM HTTP call."""

    _config: FinpipeConfig

    async def prepare_prompt(self, text: str) -> str:
        """Normalize and optionally compress prompt text for LLM inference."""
        return await prepare_llm_prompt(text, self._config.llm_prompt.compression)
