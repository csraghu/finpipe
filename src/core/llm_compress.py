"""LLMLingua prompt compression tuned for sentiment preservation."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

SENTIMENT_COMPRESSION_INSTRUCTION = (
    "Analyze the text carefully to determine the underlying market or emotional sentiment."
)
SENTIMENT_COMPRESSION_QUESTION = (
    "What is the precise sentiment, tone, and direction expressed in this text?"
)

DEFAULT_LLMLINGUA_MODEL = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"

_compressor: Any | None = None
_compressor_key: tuple[str, str] | None = None


def is_llmlingua_available() -> bool:
    try:
        import llmlingua  # noqa: F401

        return True
    except ImportError:
        return False


def reset_prompt_compressor() -> None:
    """Clear the process-wide compressor singleton (tests)."""
    global _compressor, _compressor_key
    _compressor = None
    _compressor_key = None


def _get_compressor(device: str, model_name: str = DEFAULT_LLMLINGUA_MODEL) -> Any:
    global _compressor, _compressor_key
    key = (device, model_name)
    if _compressor is None or _compressor_key != key:
        from llmlingua import PromptCompressor

        _compressor = PromptCompressor(
            model_name=model_name,
            use_llmlingua2=True,
            device_map=device,
        )
        _compressor_key = key
    return _compressor


def compress_llm_text_for_sentiment_sync(
    text: str,
    *,
    target_ratio: float = 0.5,
    device: str = "cpu",
    model_name: str = DEFAULT_LLMLINGUA_MODEL,
) -> str:
    """Compress text with LLMLingua-2 and sentiment-oriented prompt wrapping."""
    compressor = _get_compressor(device, model_name)
    wrapped = f"{SENTIMENT_COMPRESSION_INSTRUCTION}\n\n{SENTIMENT_COMPRESSION_QUESTION}\n\n{text}"
    results = compressor.compress_prompt(
        wrapped,
        rate=target_ratio,
        force_tokens=["\n", ".", "!", "?"],
    )
    compressed = results.get("compressed_prompt")
    if not isinstance(compressed, str) or not compressed.strip():
        return text
    return compressed


async def compress_llm_text_for_sentiment(
    text: str,
    *,
    target_ratio: float = 0.5,
    device: str = "cpu",
    model_name: str = DEFAULT_LLMLINGUA_MODEL,
) -> str:
    """Async wrapper — compression runs in a thread pool."""
    if not text.strip():
        return text
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: compress_llm_text_for_sentiment_sync(
            text,
            target_ratio=target_ratio,
            device=device,
            model_name=model_name,
        ),
    )
