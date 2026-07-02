"""Error taxonomy and request execution — decided in exactly ONE place.

Review fixes baked in:
- 400/404/410/422 → ``FinpipeDataNotFoundError``: never retried, never opens the breaker
- 401/403 → ``FinpipeAuthError``: never retried, never falls back
- 429 → AIMD ``record_429`` + bounded retry → ``FinpipeRateLimitExceededError``
- 5xx / network → retried with jitter → ``FinpipeProviderDownError``; only these
  count toward the circuit breaker
- URLs are sanitized before appearing in logs or exception text
- ``execute()`` gives non-HTTP providers (yfinance via sync bridge) the same
  limits/breaker/retry envelope without pretending they have status codes.

Adapters may *narrow* a classified error (add context); they must never re-map one
class to another.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from ..core.errors import (
    FinpipeAuthError,
    FinpipeDataNotFoundError,
    FinpipeError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)
from .ratelimit import AdaptiveRateLimiter, RpmTpmLimiter, TokenBucket

if TYPE_CHECKING:
    from ..core.config import HttpConfig, RateLimitConfig
    from .transport import Transport

logger = logging.getLogger(__name__)

T = TypeVar("T")

_SENSITIVE_QUERY = re.compile(r"([?&](?:key|api_?key|apikey|token|secret)=)[^&#]+", re.IGNORECASE)
_NOT_FOUND = frozenset({400, 404, 410, 422})
_AUTH = frozenset({401, 403})


def sanitize_url(url: str) -> str:
    return _SENSITIVE_QUERY.sub(r"\1<redacted>", url)


def classify(status_code: int, url: str) -> FinpipeError | None:
    """Map an HTTP status to a finpipe error, or None if the response is usable."""
    if status_code < 400:
        return None
    safe = sanitize_url(url)
    if status_code in _AUTH:
        return FinpipeAuthError(f"Provider rejected credentials ({status_code}) at {safe}")
    if status_code in _NOT_FOUND:
        return FinpipeDataNotFoundError(f"Resource not found ({status_code}) at {safe}")
    if status_code == 429:
        return FinpipeRateLimitExceededError(f"Provider throttled request (429) at {safe}")
    return FinpipeProviderDownError(f"Provider error status {status_code} at {safe}")


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (FinpipeDataNotFoundError, FinpipeAuthError)):
        return False
    return isinstance(exc, (FinpipeRateLimitExceededError, FinpipeProviderDownError))


class _Breaker:
    """Minimal circuit breaker counting ONLY FinpipeProviderDownError."""

    def __init__(self, fail_max: int, reset_timeout_sec: float) -> None:
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout_sec
        self._failures = 0
        self._opened_at: float | None = None

    def check(self, namespace: str) -> None:
        if self._opened_at is None:
            return
        if (time.monotonic() - self._opened_at) >= self._reset_timeout:
            self._opened_at = None  # half-open: allow one attempt through
            self._failures = self._fail_max - 1
            return
        raise FinpipeProviderDownError(f"Circuit open for {namespace}; failing fast")

    def record(self, exc: BaseException | None) -> None:
        if exc is None:
            self._failures = 0
            self._opened_at = None
        elif isinstance(exc, FinpipeProviderDownError):
            self._failures += 1
            if self._failures >= self._fail_max and self._opened_at is None:
                self._opened_at = time.monotonic()
                logger.error("Circuit breaker opened after %d failures", self._failures)


class RequestExecutor:
    """acquire limits → concurrency → breaker → transport → classify → bounded retry."""

    def __init__(
        self,
        namespace: str,
        transport: Transport | None,
        rate_limits: RateLimitConfig,
        *,
        db_path: str | None = None,
    ) -> None:
        self.namespace = namespace
        self._transport = transport
        self._config = rate_limits
        self.limiter = AdaptiveRateLimiter(namespace, rate_limits.max_requests_per_second, db_path)
        self._llm_limiter: RpmTpmLimiter | None = None
        self._rpm_bucket: TokenBucket | None = None
        if rate_limits.max_tokens_per_minute is not None:
            self._llm_limiter = RpmTpmLimiter(
                rpm=rate_limits.max_requests_per_minute, tpm=rate_limits.max_tokens_per_minute
            )
        elif rate_limits.max_requests_per_minute is not None:
            rpm = rate_limits.max_requests_per_minute
            self._rpm_bucket = TokenBucket(rpm / 60.0, float(rpm))
        self._breaker = _Breaker(
            rate_limits.circuit_breaker_failure_threshold,
            rate_limits.circuit_breaker_recovery_timeout_sec,
        )

    # -- limits -----------------------------------------------------------------
    async def _acquire(self, token_estimate: int | None) -> None:
        await self.limiter.acquire()
        if self._llm_limiter is not None:
            await self._llm_limiter.acquire(token_estimate or 1)
        elif self._rpm_bucket is not None:
            await self._rpm_bucket.acquire(1.0)

    async def reconcile_token_usage(self, expected: int, actual: int) -> None:
        if self._llm_limiter is not None:
            await self._llm_limiter.refund(expected, actual)

    def note_rate_limited(self) -> None:
        """Adapters call this for HTTP-200 'soft' throttle payloads (e.g. Alpha Vantage)."""
        self.limiter.record_429()

    # -- HTTP path ----------------------------------------------------------------
    async def request(
        self, method: str, url: str, *, token_estimate: int | None = None, **kwargs: Any
    ) -> Any:
        if self._transport is None:
            raise RuntimeError(f"Executor {self.namespace} has no HTTP transport")

        async def _attempt() -> Any:
            response = await self._transport.request(method, url, **kwargs)
            error = classify(response.status_code, url)
            if error is not None:
                if isinstance(error, FinpipeRateLimitExceededError):
                    self.limiter.record_429()
                raise error
            self.limiter.record_success()
            return response

        return await self._run_with_policy(_attempt, token_estimate=token_estimate, context=sanitize_url(url))

    # -- non-HTTP path (sync-bridge vendor libs like yfinance) ----------------------
    async def execute(self, operation: Callable[[], Awaitable[T]], *, context: str = "") -> T:
        """Run an arbitrary async operation under the same limits/breaker/retry policy.

        Non-finpipe exceptions are wrapped as retryable ``FinpipeProviderDownError``;
        finpipe errors raised by ``operation`` pass through classification untouched.
        """

        async def _attempt() -> T:
            try:
                result = await operation()
            except FinpipeError:
                raise
            except Exception as exc:
                wrapped = FinpipeProviderDownError(
                    f"{self.namespace} operation failed ({type(exc).__name__}): {exc}"
                )
                wrapped.__cause__ = exc
                raise wrapped from exc
            self.limiter.record_success()
            return result

        return await self._run_with_policy(_attempt, token_estimate=None, context=context or self.namespace)

    # -- shared policy loop ---------------------------------------------------------
    async def _run_with_policy(
        self,
        attempt_fn: Callable[[], Awaitable[T]],
        *,
        token_estimate: int | None,
        context: str,
    ) -> T:
        attempts = 0
        while True:
            attempts += 1
            self._breaker.check(self.namespace)
            await self._acquire(token_estimate)
            async with self.limiter.concurrency.limit():
                try:
                    result = await attempt_fn()
                except FinpipeError as error:
                    self._breaker.record(error)
                    if not is_retryable(error) or attempts > self._config.max_retries:
                        raise
                    backoff = min(10.0, 1.5**attempts) * (0.5 + random.random())
                    logger.warning(
                        "%s: retryable %s (attempt %d/%d), sleeping %.1fs — %s",
                        self.namespace, type(error).__name__, attempts,
                        self._config.max_retries, backoff, context,
                    )
                else:
                    self._breaker.record(None)
                    return result
            await asyncio.sleep(backoff)

    async def close(self) -> None:
        await self.limiter.flush()
        if self._transport is not None:
            await self._transport.close()
