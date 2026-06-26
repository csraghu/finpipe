import logging
from typing import Any

import httpx
import pybreaker
from finpipe._internal.aimd import DEFAULT_RATE_LIMIT_DB_PATH
from finpipe.core.config import CacheConfig, RateLimitConfig
from finpipe.core.exceptions import FinpipeProviderDownError, FinpipeRateLimitExceededError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .limiter import TokenBucketRateLimiter, build_adaptive_limiter

logger = logging.getLogger(__name__)


def create_circuit_breaker(config: RateLimitConfig) -> pybreaker.CircuitBreaker:
    return pybreaker.CircuitBreaker(
        fail_max=config.circuit_breaker_failure_threshold,
        reset_timeout=config.circuit_breaker_recovery_timeout_sec,
        state_storage=pybreaker.CircuitMemoryStorage(pybreaker.STATE_CLOSED),
    )


def _rate_limit_db_path(cache_config: CacheConfig | None) -> str:
    """SQLite path for learned AIMD rates (always persisted across sessions)."""
    if cache_config and cache_config.cache_type == "sqlite":
        return cache_config.sqlite_path or cache_config.sqlite_db_path
    return DEFAULT_RATE_LIMIT_DB_PATH


def rate_limit_db_path(cache_config: CacheConfig | None) -> str:
    """Public helper for non-HTTP providers (e.g. Yahoo) that build limiters directly."""
    return _rate_limit_db_path(cache_config)


def create_resilient_http_client(
    namespace: str,
    config: RateLimitConfig,
    *,
    cache_config: CacheConfig | None = None,
) -> "ResilientHttpClient":
    return ResilientHttpClient(
        namespace,
        config,
        db_path=_rate_limit_db_path(cache_config),
    )


class ResilientHttpClient:
    """
    Unified async HTTP client with AIMD rate limiting, retries, and circuit breaking.

    Uses httpx internally so tests can mock via respx; production target is curl_cffi
    (see finpipe.network.http.CurlCffiHttpClient).
    """

    def __init__(
        self,
        namespace: str,
        config: RateLimitConfig,
        *,
        db_path: str | None = None,
    ):
        self.namespace = namespace
        self._config = config
        resolved_db = db_path or DEFAULT_RATE_LIMIT_DB_PATH
        self.rate_limiter = build_adaptive_limiter(namespace, config, db_path=resolved_db)
        self._rpm_limiter: TokenBucketRateLimiter | None = None
        if config.max_requests_per_minute is not None:
            rpm_rate = config.max_requests_per_minute / 60.0
            self._rpm_limiter = TokenBucketRateLimiter(
                max_rate=rpm_rate,
                capacity=float(config.max_requests_per_minute),
            )
        self._breaker = create_circuit_breaker(config)
        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def _acquire_limits(self) -> None:
        await self.rate_limiter.acquire()
        if self._rpm_limiter is not None:
            await self._rpm_limiter.acquire(1.0)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        async def _make_request() -> httpx.Response:
            await self._acquire_limits()
            async with self.rate_limiter.concurrency.limit():
                response = await self._client.request(method, url, **kwargs)
                if response.status_code == 429:
                    self.rate_limiter.record_429()
                    response.raise_for_status()
                if response.status_code >= 400:
                    response.raise_for_status()
                self.rate_limiter.record_success()
                return response

        circuit_protected = self._breaker(_make_request)

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._config.max_retries),
                wait=wait_exponential_jitter(
                    initial=1.0, max=10.0, exp_base=self._config.backoff_multiplier
                ),
                retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.NetworkError)),
                reraise=True,
            ):
                with attempt:
                    return await circuit_protected()
        except pybreaker.CircuitBreakerError as exc:
            logger.error("Circuit breaker tripped", extra={"url": url, "namespace": self.namespace})
            raise FinpipeProviderDownError(f"Circuit breaker tripped for {url}") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                raise FinpipeRateLimitExceededError(
                    "Rate limit exhausted and max retries reached"
                ) from exc
            raise FinpipeProviderDownError(
                f"Provider returned error status: {exc.response.status_code}"
            ) from exc
        except httpx.NetworkError as exc:
            raise FinpipeProviderDownError("Provider network error") from exc

        raise RuntimeError("Unexpected resilience fallback")
