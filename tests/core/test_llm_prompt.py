from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from finpipe.core.config import LlmPromptCompressionConfig
from finpipe.core.llm_compress import (
    compress_llm_text_for_sentiment_sync,
    reset_prompt_compressor,
)
from finpipe.core.llm_prompt import prepare_llm_prompt


@pytest.fixture(autouse=True)
def _reset_compressor():
    reset_prompt_compressor()
    yield
    reset_prompt_compressor()


@pytest.mark.asyncio
async def test_prepare_llm_prompt_sanitizes_when_compression_disabled():
    compression = LlmPromptCompressionConfig(enabled=False)
    raw = "<p>Hello &#128640;</p>"
    result = await prepare_llm_prompt(raw, compression)
    assert result == "Hello"


@pytest.mark.asyncio
async def test_prepare_llm_prompt_skips_compression_below_min_chars():
    compression = LlmPromptCompressionConfig(enabled=True, min_chars=1000)
    raw = "<p>Short text</p>"
    with patch(
        "finpipe.core.llm_prompt.compress_llm_text_for_sentiment",
        new_callable=AsyncMock,
    ) as mock_compress:
        result = await prepare_llm_prompt(raw, compression)
    assert result == "Short text"
    mock_compress.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_llm_prompt_compresses_when_enabled():
    compression = LlmPromptCompressionConfig(enabled=True, min_chars=10)
    raw = "x" * 500
    with (
        patch("finpipe.core.llm_prompt.is_llmlingua_available", return_value=True),
        patch(
            "finpipe.core.llm_prompt.compress_llm_text_for_sentiment",
            new_callable=AsyncMock,
            return_value="compressed body",
        ) as mock_compress,
    ):
        result = await prepare_llm_prompt(raw, compression)
    assert result == "compressed body"
    mock_compress.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_llm_prompt_falls_back_on_compression_error():
    compression = LlmPromptCompressionConfig(enabled=True, min_chars=10)
    raw = "y" * 500
    with (
        patch("finpipe.core.llm_prompt.is_llmlingua_available", return_value=True),
        patch(
            "finpipe.core.llm_prompt.compress_llm_text_for_sentiment",
            new_callable=AsyncMock,
            side_effect=RuntimeError("model load failed"),
        ),
    ):
        result = await prepare_llm_prompt(raw, compression)
    assert result == raw


def test_compress_llm_text_for_sentiment_sync_uses_llmlingua_settings():
    mock_compressor = MagicMock()
    mock_compressor.compress_prompt.return_value = {"compressed_prompt": "kept sentiment"}
    with patch("finpipe.core.llm_compress._get_compressor", return_value=mock_compressor):
        result = compress_llm_text_for_sentiment_sync(
            "panic selling and euphoria",
            target_ratio=0.4,
            device="cpu",
        )
    assert result == "kept sentiment"
    kwargs = mock_compressor.compress_prompt.call_args.kwargs
    assert "panic selling" in mock_compressor.compress_prompt.call_args.args[0]
    assert kwargs["rate"] == 0.4


def test_compress_llm_text_for_sentiment_sync_returns_original_when_empty_result():
    mock_compressor = MagicMock()
    mock_compressor.compress_prompt.return_value = {"compressed_prompt": "   "}
    text = "fallback text"
    with patch("finpipe.core.llm_compress._get_compressor", return_value=mock_compressor):
        result = compress_llm_text_for_sentiment_sync(text)
    assert result == text


@pytest.mark.asyncio
async def test_prepare_llm_prompt_skips_when_llmlingua_missing():
    compression = LlmPromptCompressionConfig(enabled=True, min_chars=10)
    raw = "z" * 500
    with patch("finpipe.core.llm_prompt.is_llmlingua_available", return_value=False):
        result = await prepare_llm_prompt(raw, compression)
    assert result == raw
