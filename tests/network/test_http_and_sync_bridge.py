from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from finpipe.core.config import HttpConfig, RateLimitConfig
from finpipe.network.http import CurlCffiHttpClient
from finpipe.network.sync_bridge import run_sync, run_sync_callable


@pytest.fixture
def mock_limiter():
    limiter = MagicMock()
    limiter.acquire = AsyncMock()
    limiter.record_429 = MagicMock()
    limiter.record_success = MagicMock()

    @asynccontextmanager
    async def _limit():
        yield

    limiter.concurrency.limit = _limit
    return limiter


@pytest.mark.asyncio
async def test_run_sync_and_callable():
    assert await run_sync(lambda x: x + 1, 1) == 2
    assert await run_sync_callable(lambda: "ok") == "ok"


@pytest.mark.asyncio
async def test_curl_cffi_http_client_request_success(mock_limiter):
    config = HttpConfig(timeout_read_sec=5.0, user_agent="test-agent")
    limits = RateLimitConfig(max_requests_per_second=100.0)

    with patch("finpipe.network.http.build_adaptive_limiter", return_value=mock_limiter):
        with patch("finpipe.network.http.cffi_requests.AsyncSession") as session_cls:
            session = MagicMock()
            session_cls.return_value = session
            response = MagicMock()
            response.status_code = 200
            session.request = AsyncMock(return_value=response)
            client = CurlCffiHttpClient("test", config, limits, db_path=":memory:")
            result = await client.request("GET", "https://example.com")
            assert result is response
            mock_limiter.record_success.assert_called_once()
            await client.close()


@pytest.mark.asyncio
async def test_curl_cffi_http_client_429_records_limiter(mock_limiter):
    config = HttpConfig()
    limits = RateLimitConfig(max_requests_per_second=100.0)

    with patch("finpipe.network.http.build_adaptive_limiter", return_value=mock_limiter):
        with patch("finpipe.network.http.cffi_requests.AsyncSession") as session_cls:
            session = MagicMock()
            session_cls.return_value = session
            response = MagicMock()
            response.status_code = 429
            response.raise_for_status = MagicMock(side_effect=RuntimeError("rate limited"))
            session.request = AsyncMock(return_value=response)
            client = CurlCffiHttpClient("test", config, limits, db_path=":memory:")
            with pytest.raises(RuntimeError):
                await client.request("GET", "https://example.com")
            mock_limiter.record_429.assert_called_once()
            await client.close()


@pytest.mark.asyncio
async def test_curl_cffi_http_client_closed_raises(mock_limiter):
    config = HttpConfig()
    limits = RateLimitConfig(max_requests_per_second=100.0)

    with patch("finpipe.network.http.build_adaptive_limiter", return_value=mock_limiter):
        with patch("finpipe.network.http.cffi_requests.AsyncSession"):
            client = CurlCffiHttpClient("test", config, limits, db_path=":memory:")
            await client.close()
            with pytest.raises(RuntimeError, match="closed"):
                await client.request("GET", "https://example.com")


@pytest.mark.asyncio
async def test_curl_cffi_close_swallows_errors(mock_limiter):
    config = HttpConfig()
    limits = RateLimitConfig(max_requests_per_second=100.0)

    with patch("finpipe.network.http.build_adaptive_limiter", return_value=mock_limiter):
        with patch("finpipe.network.http.cffi_requests.AsyncSession") as session_cls:
            session = MagicMock()
            session.close = AsyncMock(side_effect=RuntimeError("already closed"))
            session_cls.return_value = session
            client = CurlCffiHttpClient("test", config, limits, db_path=":memory:")
            await client.close()
            assert client._session is None
