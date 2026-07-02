"""Narrow dependency injection for adapters (kills the FinpipeConfig god node).

An adapter receives a ``ProviderRuntime`` — its OWN config block, a namespaced
cache view, and a request executor. It cannot see other providers' settings,
cannot escape its cache namespace, and performs no I/O at construction time
(review: NO_CONSTRUCTOR_SIDE_EFFECTS, §2.4, graphify god-node finding).

``cached_fetch`` is the one fetch path: it caches the NORMALIZED value, so a
cache hit is byte-identical to a fresh fetch by construction (fixes review §2.3).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from ..runtime.cache import NamespacedCache
from ..runtime.resilience import RequestExecutor

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class ProviderRuntime:
    """Everything an adapter is allowed to depend on."""

    config: Any                    # this provider's config block ONLY (typed per adapter)
    cache: NamespacedCache
    executor: RequestExecutor
    dataframe_format: str = "polars"
    # Multi-source adapters (sentiment, screener) build one executor per sub-source:
    # executor_factory(sub_namespace, rate_limits, http_config) -> RequestExecutor
    executor_factory: Callable[[str, Any, Any], RequestExecutor] | None = None
    # Provider-agnostic LLM prompt settings (only LLM adapters read this)
    llm_prompt: Any | None = None


class ProviderAdapter:
    """Base adapter: lazy validation, single cached-fetch path, clean close."""

    def __init__(self, runtime: ProviderRuntime) -> None:
        self._rt = runtime
        self._validated = False

    # -- lifecycle -----------------------------------------------------------
    def _ensure_configured(self) -> None:
        """First-use validation hook. Override to check required credentials."""
        self._validated = True

    async def close(self) -> None:
        await self._rt.executor.close()

    # -- the one fetch path ---------------------------------------------------
    async def cached_fetch(
        self,
        endpoint: str,
        params: tuple[Any, ...],
        ttl_s: float,
        fetch: Callable[[], Awaitable[T]],
        *,
        stale_on_rate_limit: bool = True,
    ) -> T:
        """cache get → (miss) fetch normalized value → strict cache set → return.

        ``fetch`` must return the fully normalized value; the raw vendor payload
        is never cached. On ``FinpipeRateLimitExceededError`` an expired entry is
        returned as explicit degradation when ``stale_on_rate_limit`` is set
        (implements the documented allow_stale path that v1 never shipped).
        """
        from ..core.errors import FinpipeRateLimitExceededError

        if not self._validated:
            self._ensure_configured()

        key = self._rt.cache.key(endpoint, *params)
        cached = await self._rt.cache.get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            value = await fetch()
        except FinpipeRateLimitExceededError:
            if stale_on_rate_limit:
                stale = await self._rt.cache.get_stale(key)
                if stale is not None:
                    logger.warning("Rate limited on %s; serving stale cache entry", endpoint)
                    return stale  # type: ignore[return-value]
            raise

        await self._rt.cache.set(key, value, ttl_s)
        return value
