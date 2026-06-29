from unittest.mock import AsyncMock, patch

import pytest
from finpipe.core.config import LlmPromptCompressionConfig
from finpipe.core.llm_prompt import prepare_llm_prompt


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
    compression = LlmPromptCompressionConfig(enabled=True, min_chars=10, endpoint_url="http://test")
    raw = "x" * 500
    with patch(
        "finpipe.core.llm_prompt.compress_llm_text_for_sentiment",
        new_callable=AsyncMock,
        return_value="compressed body",
    ) as mock_compress:
        result = await prepare_llm_prompt(raw, compression)
    assert result == "compressed body"
    mock_compress.assert_awaited_once()


@pytest.mark.asyncio
async def test_prepare_llm_prompt_falls_back_on_compression_error():
    compression = LlmPromptCompressionConfig(enabled=True, min_chars=10, endpoint_url="http://test")
    raw = "y" * 500
    with patch(
        "finpipe.core.llm_prompt.compress_llm_text_for_sentiment",
        new_callable=AsyncMock,
        side_effect=RuntimeError("remote load failed"),
    ):
        result = await prepare_llm_prompt(raw, compression)
    assert result == raw


