import httpx
import pytest
import respx

from finpipe.core.config import RateLimitConfig
from finpipe.core.exceptions import FinpipeProviderDownError, FinpipeRateLimitExceededError
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
