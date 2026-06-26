import logging
from typing import Any, Self

from finpipe.catalog import CatalogService
from finpipe.catalog.adapter_registry import AdapterRegistry
from finpipe.core.config import FinpipeConfig
from finpipe.health import HealthService
from finpipe.network.cache_manager import CacheManager
from finpipe.providers.composite import (
    CompositeEquityService,
    CompositeIntelService,
    CompositeMacroService,
    CompositeOptionsService,
    CompositeScreenerService,
)

logger = logging.getLogger(__name__)


class Client:
    """Top-level facade. Public I/O is via ``client.catalog`` capability handles."""

    def __init__(self, config: FinpipeConfig | None = None):
        self.config = config or FinpipeConfig.load()
        self._ensure_registrations()

        registry = AdapterRegistry(self.config)
        self._registry = registry

        options = CompositeOptionsService(
            self.config,
            adapters=registry.options_adapters(),
        )
        self._composites = {
            "equity": CompositeEquityService(
                self.config,
                adapters=registry.equity_adapters(),
                options=options,
            ),
            "options": options,
            "macro": CompositeMacroService(
                self.config,
                fred=registry.get("fred"),
            ),
            "intel": CompositeIntelService(
                self.config,
                sentiment=registry.get("sentiment"),
            ),
            "screener": CompositeScreenerService(
                self.config,
                screener=registry.get("screener"),
            ),
        }

        self.catalog = CatalogService(self)
        self.health = HealthService(self)

    @staticmethod
    def _ensure_registrations() -> None:
        import finpipe.providers  # noqa: F401 — side-effect registration

    async def close(self) -> None:
        await self._registry.close()
        if self.config.cache.singleton:
            CacheManager.shutdown()
        logger.info("Finpipe client gracefully shut down.")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    def dump_settings(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        """Return resolved settings for all capability and provider interfaces."""
        return self.config.dump_settings(redact_secrets=redact_secrets)

    def dump_settings_json(self, *, indent: int = 2, redact_secrets: bool = True) -> str:
        """Serialize resolved settings for all capability and provider interfaces."""
        return self.config.dump_settings_json(indent=indent, redact_secrets=redact_secrets)
