from __future__ import annotations

import logging
from typing import Any, Literal, cast

from curl_cffi import requests as cffi_requests
from finpipe.core.config import HttpConfig, RateLimitConfig
from finpipe.network.limiter import AdaptiveRateLimiter, build_adaptive_limiter
from finpipe.network.resilience import rate_limit_db_path

logger = logging.getLogger(__name__)


class CurlCffiHttpClient:
    """curl_cffi async HTTP wrapper with AIMD rate limiting."""

    def __init__(
        self,
        namespace: str,
        config: HttpConfig,
        rate_limits: RateLimitConfig,
        *,
        db_path: str | None = None,
    ):
        self.namespace = namespace
        self._config = config
        self.rate_limiter: AdaptiveRateLimiter = build_adaptive_limiter(
            namespace,
            rate_limits,
            db_path=db_path or rate_limit_db_path(None),
        )
        self._session: cffi_requests.AsyncSession | None = cffi_requests.AsyncSession(
            timeout=config.timeout_read_sec,
            impersonate=cast(Any, config.impersonate or "chrome124"),
            headers={"User-Agent": config.user_agent or "finpipe/0.1"},
        )

    async def close(self) -> None:
        client = self._session
        if client is None:
            return
        self._session = None
        try:
            await client.close()
        except (TypeError, RuntimeError) as exc:
            logger.debug("Ignored error closing curl_cffi session: %s", exc)

    async def request(
        self, method: Literal["GET", "POST", "PUT", "DELETE"], url: str, **kwargs: Any
    ) -> Any:
        await self.rate_limiter.acquire()
        async with self.rate_limiter.concurrency.limit():
            client = self._session
            if client is None:
                raise RuntimeError(f"HTTP client ({self.namespace}) is closed")
            response = await client.request(method, url, **kwargs)
            if response.status_code == 429:
                self.rate_limiter.record_429()
                response.raise_for_status()
            if response.status_code >= 400:
                response.raise_for_status()
            self.rate_limiter.record_success()
            return response
