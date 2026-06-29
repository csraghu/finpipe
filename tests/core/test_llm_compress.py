import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from finpipe.core.llm_compress import compress_llm_text_for_sentiment
from finpipe.core.llm_prompt import prepare_llm_prompt, prepare_gemini_prompt
from finpipe.core.config import LlmPromptCompressionConfig

@pytest.mark.asyncio
async def test_compress_llm_text_for_sentiment_empty():
    assert await compress_llm_text_for_sentiment("   ") == "   "

@pytest.mark.asyncio
async def test_compress_llm_text_for_sentiment_with_http_client():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"compressed_prompt": "compressed text"}
    mock_client.request.return_value = mock_resp
    
    with patch.dict("os.environ", {"PROMPRESS_API_KEY": "test_key"}):
        result = await compress_llm_text_for_sentiment(
            "some text to compress",
            endpoint_url="http://fake",
            http_client=mock_client
        )
        assert result == "compressed text"
        mock_client.request.assert_called_once()

@pytest.mark.asyncio
async def test_compress_llm_text_for_sentiment_with_httpx():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"compressed_prompt": "compressed text"}
        mock_client.post.return_value = mock_resp
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        
        result = await compress_llm_text_for_sentiment(
            "some text to compress",
            endpoint_url="http://fake",
        )
        assert result == "compressed text"
        mock_client.post.assert_called_once()

@pytest.mark.asyncio
async def test_compress_llm_text_for_sentiment_httpx_exception():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("HTTP Error")
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        
        result = await compress_llm_text_for_sentiment(
            "some text to compress",
            endpoint_url="http://fake",
        )
        assert result == "some text to compress"

@pytest.mark.asyncio
async def test_prepare_llm_prompt_no_endpoint():
    config = LlmPromptCompressionConfig(enabled=True, min_chars=10, endpoint_url=None)
    result = await prepare_llm_prompt("this is a test string", config)
    assert result == "this is a test string"

@pytest.mark.asyncio
async def test_prepare_gemini_prompt():
    config = LlmPromptCompressionConfig(enabled=False)
    result = await prepare_gemini_prompt("text", config)
    assert result == "text"

@pytest.mark.asyncio
async def test_prepare_llm_prompt_exception():
    config = LlmPromptCompressionConfig(enabled=True, min_chars=10, endpoint_url="http://fake")
    with patch("finpipe.core.llm_prompt.compress_llm_text_for_sentiment") as mock_compress:
        mock_compress.side_effect = Exception("Compression Error")
        result = await prepare_llm_prompt("this is a test string", config)
        assert result == "this is a test string"
