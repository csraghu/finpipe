"""Prepare prompts for LLM providers: sanitize, then optional LLMLingua compression."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from finpipe.core.llm_compress import (
    compress_llm_text_for_sentiment,
)
from finpipe.core.llm_sanitize import sanitize_llm_text

if TYPE_CHECKING:
    from finpipe.core.config import LlmPromptCompressionConfig

logger = logging.getLogger(__name__)


async def prepare_llm_prompt(
    text: str,
    compression: LlmPromptCompressionConfig,
) -> str:
    """Sanitize noise, then optionally compress with sentiment-aware LLMLingua."""
    prepared = sanitize_llm_text(text)
    if not compression.enabled:
        return prepared
    if len(prepared) < compression.min_chars:
        return prepared
    if not compression.endpoint_url:
        logger.warning("Prompt compression enabled but no endpoint_url configured. Skipping compression.")
        return prepared
    try:
        return await compress_llm_text_for_sentiment(
            prepared,
            target_ratio=compression.target_ratio,
            device=compression.device,
            model_name=compression.model_name,
            endpoint_url=compression.endpoint_url,
        )
    except Exception as exc:
        logger.warning(
            "LLM prompt compression failed; using sanitized text only",
            extra={"error": str(exc)},
        )
        return prepared


async def prepare_gemini_prompt(text: str, compression: LlmPromptCompressionConfig) -> str:
    """Backward-compatible alias for :func:`prepare_llm_prompt`."""
    return await prepare_llm_prompt(text, compression)
