"""Taxonomy contract tests: status → exception → retried? (the v1 gap in one table)."""

from __future__ import annotations

import pytest
from finpipe.core.config import RateLimitConfig
from finpipe.core.errors import (
    FinpipeAuthError,
    FinpipeDataNotFoundError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)
from finpipe.runtime.resilience import RequestExecutor, classify, is_retryable, sanitize_url

from conftest import FakeResponse, FakeTransport


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, None),
        (400, FinpipeDataNotFoundError),
        (404, FinpipeDataNotFoundError),
        (401, FinpipeAuthError),
        (403, FinpipeAuthError),
        (429, FinpipeRateLimitExceededError),
        (500, FinpipeProviderDownError),
        (503, FinpipeProviderDownError),
    ],
)
def test_classification_table(status, expected):
    result = classify(status, "https://api.example.com/x")
    if expected is None:
        assert result is None
    else:
        assert isinstance(result, expected)


def test_retry_matrix():
    assert not is_retryable(FinpipeDataNotFoundError("x"))
    assert not is_retryable(FinpipeAuthError("x"))
    assert is_retryable(FinpipeRateLimitExceededError("x"))
    assert is_retryable(FinpipeProviderDownError("x"))


def test_sanitize_url_strips_secrets():
    url = "https://api.example.com/v1?symbol=AAPL&api_key=SUPERSECRET&x=1"
    cleaned = sanitize_url(url)
    assert "SUPERSECRET" not in cleaned
    assert "symbol=AAPL" in cleaned


def _executor(responses, *, max_retries: int = 2, rps: float = 1000.0) -> tuple[RequestExecutor, FakeTransport]:
    transport = FakeTransport(responses)
    config = RateLimitConfig(max_requests_per_second=rps, max_retries=max_retries)
    return RequestExecutor("test_ns", transport, config), transport


async def test_404_is_not_retried():
    executor, transport = _executor([FakeResponse(404)])
    with pytest.raises(FinpipeDataNotFoundError):
        await executor.request("GET", "https://api.example.com/x")
    assert len(transport.calls) == 1  # v1 retried this 3 times


async def test_5xx_retries_then_provider_down():
    executor, transport = _executor([FakeResponse(500)] * 3, max_retries=2)
    with pytest.raises(FinpipeProviderDownError):
        await executor.request("GET", "https://api.example.com/x")
    assert len(transport.calls) == 3  # initial + 2 retries


async def test_5xx_then_success_recovers():
    executor, transport = _executor([FakeResponse(500), FakeResponse(200, json_data={"ok": True})])
    response = await executor.request("GET", "https://api.example.com/x")
    assert response.json() == {"ok": True}
    assert len(transport.calls) == 2


async def test_429_backs_off_aimd_and_raises_rate_limit():
    executor, transport = _executor([FakeResponse(429)] * 3, max_retries=2)
    rate_before = executor.limiter.rate
    with pytest.raises(FinpipeRateLimitExceededError):
        await executor.request("GET", "https://api.example.com/x")
    assert executor.limiter.rate < rate_before  # record_429 fired


async def test_breaker_opens_after_repeated_provider_down_and_fails_fast():
    responses = [FakeResponse(500)] * 30
    transport = FakeTransport(responses)
    config = RateLimitConfig(
        max_requests_per_second=1000.0,
        max_retries=0,
        circuit_breaker_failure_threshold=3,
        circuit_breaker_recovery_timeout_sec=60.0,
    )
    executor = RequestExecutor("breaker_ns", transport, config)
    for _ in range(3):
        with pytest.raises(FinpipeProviderDownError):
            await executor.request("GET", "https://api.example.com/x")
    calls_before = len(transport.calls)
    with pytest.raises(FinpipeProviderDownError, match="Circuit open"):
        await executor.request("GET", "https://api.example.com/x")
    assert len(transport.calls) == calls_before  # failed fast, no transport call


async def test_execute_wraps_arbitrary_errors_as_provider_down():
    executor, _ = _executor([], max_retries=0)

    async def boom():
        raise ValueError("vendor exploded")

    with pytest.raises(FinpipeProviderDownError, match="vendor exploded"):
        await executor.execute(lambda: boom())


async def test_execute_passes_finpipe_errors_through():
    executor, _ = _executor([], max_retries=0)

    async def missing():
        raise FinpipeDataNotFoundError("no such ticker")

    with pytest.raises(FinpipeDataNotFoundError):
        await executor.execute(lambda: missing())
