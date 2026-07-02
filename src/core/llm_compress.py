"""LLMLingua prompt compression tuned for sentiment preservation."""

from __future__ import annotations

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

def _chunk_text(text: str, max_words: int = 300) -> list[str]:
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    chunks = []
    current_chunk = []
    current_length = 0

    for p in paragraphs:
        p_len = len(p.split())
        if current_length + p_len > max_words and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_length = 0

        if p_len > max_words:
            words = p.split()
            for i in range(0, len(words), max_words):
                chunks.append(" ".join(words[i:i + max_words]))
        else:
            current_chunk.append(p)
            current_length += p_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks

async def compress_llm_text_for_sentiment(
    text: str,
    *,
    target_ratio: float = 0.5,
    device: str = "cpu",
    model_name: str = DEFAULT_LLMLINGUA_MODEL,
    endpoint_url: str | None = None,
    http_client: Any = None,
    symbol: str | None = None,
) -> str:
    """Async wrapper — compression runs via remote API."""
    if not text.strip():
        return text

    if not endpoint_url:
        return text

    import os
    headers = {}
    api_key = os.environ.get("PROMPRESS_API_KEY") or os.environ.get("HF_TOKEN")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    chunks = _chunk_text(text, max_words=300)
    compressed_chunks = []

    for chunk in chunks:
        try:
            payload = {
                "text": chunk,
                "target_ratio": target_ratio,
                "model_name": model_name,
            }
            if symbol:
                payload["symbol"] = symbol
            if http_client is not None:
                resp = await http_client.request("POST", endpoint_url, json=payload, headers=headers)
                data = resp.json()
            else:
                import httpx
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(endpoint_url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()

            compressed = data.get("compressed_prompt")
            if isinstance(compressed, str) and compressed.strip():
                compressed_chunks.append(compressed)
            else:
                compressed_chunks.append(chunk)
        except Exception as exc:
            logger.warning("Remote LLMLingua compression failed for chunk, keeping uncompressed text: %s", exc)
            compressed_chunks.append(chunk)

    return "\n".join(compressed_chunks)
