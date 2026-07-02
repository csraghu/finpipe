"""Top-level Client facade.

Guarantees (all review fixes):
- constructing ``Client()`` performs ZERO I/O and ZERO credential validation
- capability services are typed attributes — autocomplete and type-checkers work
- adapters build lazily on first use; a client configured only for Yahoo needs
  no other API keys
- ``client.catalog`` is introspection-only; ``client.health`` reports per-provider
  status; both are derived from the provider manifest registry.
"""

from __future__ import annotations

import logging
from functools import cached_property
from typing import TYPE_CHECKING, Any, Self

from .core.config import FinpipeConfig
from .providers.wiring import AdapterPool, ensure_provider_modules_loaded

if TYPE_CHECKING:
    from .capabilities.equity import EquityService
    from .capabilities.intel import IntelService
    from .capabilities.llm import LlmService
    from .capabilities.macro import MacroService
    from .capabilities.options import OptionsService
    from .capabilities.screener import ScreenerService
    from .observe.catalog import CatalogService
    from .observe.health import HealthReport, HealthService

logger = logging.getLogger(__name__)


class Client:
    def __init__(self, config: FinpipeConfig | None = None) -> None:
        self.config = config or FinpipeConfig.load()
        ensure_provider_modules_loaded()
        self._pool = AdapterPool(self.config)

    # -- typed capability services (lazy; no I/O until a method is awaited) --------
    @cached_property
    def equity(self) -> EquityService:
        from .capabilities.equity import EquityService

        return EquityService(self._pool, self.config)

    @cached_property
    def options(self) -> OptionsService:
        from .capabilities.options import OptionsService

        return OptionsService(self._pool, self.config)

    @cached_property
    def macro(self) -> MacroService:
        from .capabilities.macro import MacroService

        return MacroService(self._pool, self.config)

    @cached_property
    def intel(self) -> IntelService:
        from .capabilities.intel import IntelService

        return IntelService(self._pool, self.config)

    @cached_property
    def screener(self) -> ScreenerService:
        from .capabilities.screener import ScreenerService

        return ScreenerService(self._pool, self.config)

    @cached_property
    def llm(self) -> LlmService:
        from .capabilities.llm import LlmService

        return LlmService(self._pool, self.config)

    # -- introspection ---------------------------------------------------------------
    @cached_property
    def catalog(self) -> CatalogService:
        from .observe.catalog import CatalogService

        return CatalogService(self)

    @cached_property
    def health(self) -> HealthService:
        from .observe.health import HealthService

        return HealthService(self)

    async def health_check(self) -> HealthReport:
        return await self.health.ping()

    def dump_settings(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        from .observe.settings_dump import dump_settings

        return dump_settings(self.config, redact_secrets=redact_secrets)

    def dump_settings_json(self, *, indent: int = 2, redact_secrets: bool = True) -> str:
        from .observe.settings_dump import dump_settings_json

        return dump_settings_json(self.config, indent=indent, redact_secrets=redact_secrets)

    # -- lifecycle ----------------------------------------------------------------------
    async def close(self) -> None:
        await self._pool.close()
        if self.config.cache.singleton:
            from .runtime.cache import CacheManager

            CacheManager.shutdown()
        logger.info("finpipe client shut down")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
