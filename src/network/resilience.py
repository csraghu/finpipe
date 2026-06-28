import logging
from typing import Any, cast

import httpx
import pybreaker
from curl_cffi import requests as cffi_requests
from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
from finpipe._internal.aimd import DEFAULT_RATE_LIMIT_DB_PATH
from finpipe.core.config import CacheConfig, HttpConfig, RateLimitConfig
from finpipe.core.exceptions import FinpipeProviderDownError, FinpipeRateLimitExceededError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .limiter import RpmTpmRateLimiter, TokenBucketRateLimiter, build_adaptive_limiter

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
    http: HttpConfig | None = None,
) -> "ResilientHttpClient":
    return ResilientHttpClient(
        namespace,
        config,
        db_path=_rate_limit_db_path(cache_config),
        http=http,
    )


def _http_error_status(exc: BaseException) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    response = getattr(exc, "response", None)
    if response is not None:
        return getattr(response, "status_code", None)
    return None


def _http_error_body_snippet(exc: BaseException, *, max_len: int = 300) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    try:
        text = response.text
    except Exception:
        return ""
    if not text:
        return ""
    snippet = text.strip().replace("\n", " ")
    if len(snippet) > max_len:
        snippet = snippet[:max_len] + "…"
    return snippet


def _format_http_status_error(status_code: int | None, exc: BaseException) -> str:
    msg = f"Provider returned error status: {status_code}"
    snippet = _http_error_body_snippet(exc)
    if snippet:
        msg = f"{msg}: {snippet}"
    return msg


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
        http: HttpConfig | None = None,
    ):
        self.namespace = namespace
        self._config = config
        self._http = http or HttpConfig()
        resolved_db = db_path or DEFAULT_RATE_LIMIT_DB_PATH
        self.rate_limiter = build_adaptive_limiter(namespace, config, db_path=resolved_db)
        self._rpm_limiter: TokenBucketRateLimiter | None = None
        self._llm_limiter: RpmTpmRateLimiter | None = None
        if config.max_tokens_per_minute is not None:
            self._llm_limiter = RpmTpmRateLimiter(
                rpm=config.max_requests_per_minute,
                tpm=config.max_tokens_per_minute,
            )
        elif config.max_requests_per_minute is not None:
            rpm_rate = config.max_requests_per_minute / 60.0
            self._rpm_limiter = TokenBucketRateLimiter(
                max_rate=rpm_rate,
                capacity=float(config.max_requests_per_minute),
            )
        self._breaker = create_circuit_breaker(config)
        self._httpx_client: httpx.AsyncClient | None = None
        self._curl_session: cffi_requests.AsyncSession | None = None
        if self._http.transport == "curl_cffi":
            session_headers: dict[str, str] = {}
            if self._http.user_agent:
                session_headers["User-Agent"] = self._http.user_agent
            self._curl_session = cffi_requests.AsyncSession(
                timeout=self._http.timeout_read_sec,
                impersonate=cast(Any, self._http.impersonate or "chrome124"),
                headers=session_headers or None,
            )
        else:
            timeout = httpx.Timeout(
                self._http.timeout_read_sec,
                connect=self._http.timeout_connect_sec,
            )
            self._httpx_client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)

    async def close(self) -> None:
        if self._httpx_client is not None:
            await self._httpx_client.aclose()
            self._httpx_client = None
        if self._curl_session is not None:
            session = self._curl_session
            self._curl_session = None
            try:
                await session.close()
            except (TypeError, RuntimeError) as exc:
                logger.debug("Ignored error closing curl_cffi session: %s", exc)

    async def _acquire_limits(self, token_estimate: int | None = None) -> None:
        await self.rate_limiter.acquire()
        if self._llm_limiter is not None:
            tokens = max(1, token_estimate) if token_estimate is not None else 1
            await self._llm_limiter.acquire(tokens)
        elif self._rpm_limiter is not None:
            await self._rpm_limiter.acquire(1.0)

    async def reconcile_token_usage(self, expected: int, actual: int) -> None:
        if self._llm_limiter is not None:
            await self._llm_limiter.update_actual_tokens(expected, actual)

    async def _transport_request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self._curl_session is not None:
            return await self._curl_session.request(method, url, **kwargs)
        if self._httpx_client is None:
            raise RuntimeError(f"HTTP client ({self.namespace}) is closed")
        return await self._httpx_client.request(method, url, **kwargs)

    async def request(
        self,
        method: str,
        url: str,
        *,
        token_estimate: int | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        async def _make_request() -> Any:
            await self._acquire_limits(token_estimate)
            async with self.rate_limiter.concurrency.limit():
                response = await self._transport_request(method, url, **kwargs)
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
                retry=retry_if_exception_type(
                    (httpx.HTTPStatusError, httpx.NetworkError, CurlHTTPError)
                ),
                reraise=True,
            ):
                with attempt:
                    return await circuit_protected()
        except pybreaker.CircuitBreakerError as exc:
            logger.error("Circuit breaker tripped", extra={"url": url, "namespace": self.namespace})
            raise FinpipeProviderDownError(f"Circuit breaker tripped for {url}") from exc
        except (httpx.HTTPStatusError, CurlHTTPError) as exc:
            status_code = _http_error_status(exc)
            if status_code == 429:
                raise FinpipeRateLimitExceededError(
                    "Rate limit exhausted and max retries reached"
                ) from exc
            raise FinpipeProviderDownError(_format_http_status_error(status_code, exc)) from exc
        except httpx.NetworkError as exc:
            raise FinpipeProviderDownError("Provider network error") from exc

        raise RuntimeError("Unexpected resilience fallback")
