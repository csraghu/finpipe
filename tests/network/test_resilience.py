import asyncio

import httpx
import pytest
import respx
from finpipe.core.config import RateLimitConfig
from finpipe.core.exceptions import FinpipeProviderDownError, FinpipeRateLimitExceededError
from finpipe.network.limiter import RpmTpmRateLimiter
from finpipe.network.resilience import ResilientHttpClient


@pytest.mark.asyncio
async def test_resilient_http_client_success():
    config = RateLimitConfig(max_requests_per_second=10)
    client = ResilientHttpClient("test", config)

    with respx.mock:
        route = respx.get("https://test.com").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        resp = await client.request("GET", "https://test.com")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        assert route.called


@pytest.mark.asyncio
async def test_resilient_http_client_rate_limit():
    config = RateLimitConfig(max_requests_per_second=10, max_retries=1)  # Fast fail
    client = ResilientHttpClient("test", config)

    with respx.mock:
        respx.get("https://test.com").mock(return_value=httpx.Response(429))
        with pytest.raises(FinpipeRateLimitExceededError):
            await client.request("GET", "https://test.com")


@pytest.mark.asyncio
async def test_resilient_http_client_circuit_breaker():
    config = RateLimitConfig(
        max_requests_per_second=10, circuit_breaker_failure_threshold=2, max_retries=0
    )
    client = ResilientHttpClient("test", config)

    with respx.mock:
        respx.get("https://test.com").mock(return_value=httpx.Response(500))

        # Trip the breaker
        with pytest.raises(FinpipeProviderDownError):
            await client.request("GET", "https://test.com")
        with pytest.raises(FinpipeProviderDownError):
            await client.request("GET", "https://test.com")

        # Third one should fail via PyBreaker (or max retries)
        with pytest.raises(FinpipeProviderDownError):
            await client.request("GET", "https://test.com")


@pytest.mark.asyncio
async def test_resilient_http_client_max_retries():
    config = RateLimitConfig(
        max_requests_per_second=10, max_retries=1, circuit_breaker_failure_threshold=5
    )
    client = ResilientHttpClient("test", config)

    with respx.mock:
        respx.get("https://test.com").mock(return_value=httpx.Response(429))

        with pytest.raises(FinpipeRateLimitExceededError):
            await client.request("GET", "https://test.com")


@pytest.mark.asyncio
async def test_resilient_http_client_network_error():
    config = RateLimitConfig(
        max_requests_per_second=10, max_retries=0, circuit_breaker_failure_threshold=5
    )
    client = ResilientHttpClient("test", config)

    with respx.mock:
        respx.get("https://test.com").mock(side_effect=httpx.NetworkError("Connection closed"))

        with pytest.raises(FinpipeProviderDownError, match="network error"):
            await client.request("GET", "https://test.com")


@pytest.mark.asyncio
async def test_resilient_http_client_uses_llm_limiter_when_tpm_configured():
    config = RateLimitConfig(
        max_requests_per_second=10,
        max_requests_per_minute=60,
        max_tokens_per_minute=1000,
    )
    client = ResilientHttpClient("test", config)

    assert client._llm_limiter is not None
    assert isinstance(client._llm_limiter, RpmTpmRateLimiter)
    assert client._rpm_limiter is None


@pytest.mark.asyncio
async def test_resilient_http_client_large_token_estimate_depletes_bucket():
    config = RateLimitConfig(
        max_requests_per_second=100,
        max_tokens_per_minute=200,
    )
    client = ResilientHttpClient("test", config)
    assert client._llm_limiter is not None

    with respx.mock:
        respx.get("https://test.com").mock(return_value=httpx.Response(200, json={"ok": True}))
        await client.request("GET", "https://test.com", token_estimate=150)
        assert client._llm_limiter.tok_tokens == pytest.approx(50.0)

        start = asyncio.get_event_loop().time()
        await client.request("GET", "https://test.com", token_estimate=100)
        duration = asyncio.get_event_loop().time() - start
        assert duration >= 0.05
        assert client._llm_limiter.tok_tokens == pytest.approx(0.0, abs=1.0)
