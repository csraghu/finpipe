"""Builds ProviderRuntime objects and lazily constructs adapters.

This is the ONLY place that sees both ``FinpipeConfig`` and adapters. Adapters
receive their narrow ``ProviderRuntime``; nothing downstream touches the full
config (kills the god node). Construction is lazy and cached per provider key
(fixes eager Client() I/O and eager credential validation).
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.config import FinpipeConfig, HttpConfig, RateLimitConfig
from ..runtime.cache import NamespacedCache, resolve_cache_backend
from ..runtime.resilience import RequestExecutor
from ..runtime.transport import create_transport
from .base import ProviderRuntime
from .manifest import REGISTRY, ProviderManifest

logger = logging.getLogger(__name__)


class RuntimeFactory:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config
        self._backend = None  # created on first cache use, not at Client()

    def _cache_backend(self):
        if self._backend is None:
            self._backend = resolve_cache_backend(self._config.cache)
        return self._backend

    def _make_executor(
        self, namespace: str, rate_limits: RateLimitConfig, http: HttpConfig | None
    ) -> RequestExecutor:
        transport = create_transport(http) if http is not None else None
        return RequestExecutor(namespace, transport, rate_limits)

    def runtime_for(self, manifest: ProviderManifest) -> ProviderRuntime:
        provider_config = getattr(self._config.providers, manifest.config_attr)
        http = getattr(provider_config, "http", None)
        rate_limits = getattr(provider_config, "rate_limits", None) or RateLimitConfig()
        return ProviderRuntime(
            config=provider_config,
            cache=NamespacedCache(self._cache_backend(), self._config.cache.namespace, manifest.key),
            executor=self._make_executor(manifest.key, rate_limits, http),
            dataframe_format=self._config.dataframe_format,
            executor_factory=self._make_executor,
            llm_prompt=self._config.llm_prompt,
        )


class AdapterPool:
    """Lazy adapter construction, one instance per provider key, clean close."""

    def __init__(self, config: FinpipeConfig) -> None:
        self._factory = RuntimeFactory(config)
        self._config = config
        self._adapters: dict[str, Any] = {}

    def get(self, key: str) -> Any:
        adapter = self._adapters.get(key)
        if adapter is None:
            manifest = REGISTRY.get(key)
            provider_config = getattr(self._config.providers, manifest.config_attr)
            if not getattr(provider_config, "enabled", True):
                from ..core.errors import FinpipeConfigError

                raise FinpipeConfigError(f"Provider {key!r} is disabled in settings")
            adapter = manifest.factory(self._factory.runtime_for(manifest))
            self._adapters[key] = adapter
        return adapter

    def get_if_enabled(self, key: str) -> Any | None:
        manifest = REGISTRY.get(key)
        provider_config = getattr(self._config.providers, manifest.config_attr)
        if not getattr(provider_config, "enabled", True):
            return None
        return self.get(key)

    def built(self) -> dict[str, Any]:
        return dict(self._adapters)

    async def close(self) -> None:
        for key, adapter in list(self._adapters.items()):
            try:
                await adapter.close()
            except Exception:  # pragma: no cover — defensive shutdown
                logger.warning("Error closing adapter %s", key, exc_info=True)
        self._adapters.clear()


def ensure_provider_modules_loaded() -> None:
    """Import adapter modules so their @provider manifests register (lazy, one-shot)."""
    from . import alpha_vantage, fred, massive, screener, sentiment, yahoo  # noqa: F401
    from .llm import gemini, openai_compat  # noqa: F401
