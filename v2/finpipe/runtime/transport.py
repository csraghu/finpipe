"""Pluggable async HTTP transports (the seam the v1 docs promised but never built).

- ``HttpxTransport`` — keyed REST APIs (respx-mockable in tests)
- ``CurlCffiTransport`` — scraping/anti-bot endpoints (browser TLS impersonation);
  curl_cffi is imported lazily so environments without it can still use httpx paths.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from ..core.config import HttpConfig

logger = logging.getLogger(__name__)


class Transport(Protocol):
    async def request(self, method: str, url: str, **kwargs: Any) -> Any: ...
    async def close(self) -> None: ...


class HttpxTransport:
    def __init__(self, http: HttpConfig) -> None:
        import httpx

        headers = {"User-Agent": http.user_agent} if http.user_agent else None
        self._client: Any = httpx.AsyncClient(
            timeout=httpx.Timeout(http.timeout_read_sec, connect=http.timeout_connect_sec),
            follow_redirects=True,
            headers=headers,
        )

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self._client is None:
            raise RuntimeError("Transport is closed")
        return await self._client.request(method, url, **kwargs)

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()


class CurlCffiTransport:
    def __init__(self, http: HttpConfig) -> None:
        from curl_cffi import requests as cffi_requests  # lazy: optional-ish dep

        headers = {"User-Agent": http.user_agent} if http.user_agent else None
        self._session: Any = cffi_requests.AsyncSession(
            timeout=http.timeout_read_sec,
            impersonate=http.impersonate or "chrome124",  # type: ignore[arg-type]
            headers=headers,
        )

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self._session is None:
            raise RuntimeError("Transport is closed")
        return await self._session.request(method, url, **kwargs)

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        try:
            await session.close()
        except (TypeError, RuntimeError) as exc:  # curl_cffi teardown quirk on Windows
            logger.debug("Ignored curl_cffi close error: %s", exc)


def create_transport(http: HttpConfig) -> Transport:
    if http.transport == "curl_cffi":
        return CurlCffiTransport(http)
    return HttpxTransport(http)
