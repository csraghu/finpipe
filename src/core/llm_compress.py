"""LLMLingua prompt compression tuned for sentiment preservation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SENTIMENT_COMPRESSION_INSTRUCTION = (
    "Analyze the text carefully to determine the underlying market or emotional sentiment."
)
SENTIMENT_COMPRESSION_QUESTION = (
    "What is the precise sentiment, tone, and direction expressed in this text?"
)

DEFAULT_LLMLINGUA_MODEL = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"

async def compress_llm_text_for_sentiment(
    text: str,
    *,
    target_ratio: float = 0.5,
    device: str = "cpu",
    model_name: str = DEFAULT_LLMLINGUA_MODEL,
    endpoint_url: str | None = None,
) -> str:
    """Async wrapper — compression runs via remote API."""
    if not text.strip():
        return text

    if endpoint_url:
        import os

        import httpx
        headers = {}
        api_key = os.environ.get("PROMPRESS_API_KEY") or os.environ.get("HF_TOKEN")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    endpoint_url,
                    json={
                        "text": text,
                        "target_ratio": target_ratio,
                        "model_name": model_name,
                    },
                    headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                compressed = data.get("compressed_prompt")
                if isinstance(compressed, str) and compressed.strip():
                    return compressed
        except Exception as exc:
            logger.warning("Remote LLMLingua compression failed, returning uncompressed text: %s", exc)

    return text
