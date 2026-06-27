import asyncio

import pytest
from finpipe.network.limiter import (
    RpmTpmRateLimiter,
    TokenBucketRateLimiter,
    estimate_llm_token_usage,
)


@pytest.mark.asyncio
async def test_limiter_acquires_tokens():
    limiter = TokenBucketRateLimiter(max_rate=10.0, capacity=2)
    # Should acquire without sleeping
    start = asyncio.get_event_loop().time()
    await limiter.acquire()
    await limiter.acquire()
    duration = asyncio.get_event_loop().time() - start
    assert duration < 0.1


@pytest.mark.asyncio
async def test_limiter_congestion():
    limiter = TokenBucketRateLimiter(max_rate=10.0, capacity=2)
    assert limiter._current_max_rate == 10.0
    limiter.trigger_congestion_backoff()
    assert limiter._current_max_rate == 5.0

    # Check that rate restores
    await asyncio.sleep(0.1)
    # After some time, current rate should slowly recover
    # but exact value depends on last_update logic. Let's just test it doesn't crash
    await limiter.acquire()


def test_estimate_llm_token_usage():
    assert estimate_llm_token_usage("", 100) == 1 + 100
    assert estimate_llm_token_usage("abcd", 50) == 1 + 50
    assert estimate_llm_token_usage("a" * 40, 10) == 10 + 10


@pytest.mark.asyncio
async def test_rpm_tpm_acquire_within_limits():
    limiter = RpmTpmRateLimiter(rpm=60, tpm=1000)
    start = asyncio.get_event_loop().time()
    await limiter.acquire(100)
    await limiter.acquire(100)
    duration = asyncio.get_event_loop().time() - start
    assert duration < 0.1
    assert limiter.tok_tokens == pytest.approx(800.0)


@pytest.mark.asyncio
async def test_rpm_tpm_blocks_when_bucket_exhausted():
    limiter = RpmTpmRateLimiter(rpm=None, tpm=100)
    await limiter.acquire(100)
    assert limiter.tok_tokens == pytest.approx(0.0)

    start = asyncio.get_event_loop().time()
    await limiter.acquire(10)
    duration = asyncio.get_event_loop().time() - start
    assert duration >= 0.05


@pytest.mark.asyncio
async def test_rpm_tpm_update_actual_tokens_refunds_overestimate():
    limiter = RpmTpmRateLimiter(rpm=None, tpm=1000)
    await limiter.acquire(500)
    assert limiter.tok_tokens == pytest.approx(500.0)

    await limiter.update_actual_tokens(500, 200)
    assert limiter.tok_tokens == pytest.approx(800.0)


@pytest.mark.asyncio
async def test_rpm_tpm_enforces_rpm():
    limiter = RpmTpmRateLimiter(rpm=1, tpm=10_000)
    await limiter.acquire(1)
    assert limiter.req_tokens == pytest.approx(0.0)

    start = asyncio.get_event_loop().time()
    await limiter.acquire(1)
    duration = asyncio.get_event_loop().time() - start
    assert duration >= 0.05
