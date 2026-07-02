"""Typed options capability service (protocol methods only).

Massive-specific surface (S3 flatfiles, raw snapshot API, historical aggs) is
NOT smeared across all options providers as fake stubs like v1 did — reach it
explicitly via ``client.catalog.provider("massive").adapter()``.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from ..core.models import OptionChain
from ..core.protocols import DataFrameLike
from .equity import build_route
from .policy import call_with_fallback

if TYPE_CHECKING:
    from ..core.config import FinpipeConfig
    from ..providers.wiring import AdapterPool


class OptionsService:
    def __init__(self, pool: AdapterPool, config: FinpipeConfig) -> None:
        self._pool = pool
        self._route = build_route(config, "options_primary", "options_fallback")

    def _attempts(self, method: str, *args: Any, **kwargs: Any):
        attempts = []
        for name in self._route:
            adapter = self._pool.get_if_enabled(name)
            if adapter is None or not hasattr(adapter, method):
                continue
            attempts.append((name, lambda a=adapter, m=method: getattr(a, m)(*args, **kwargs)))
        return attempts

    async def get_options_chain(self, symbol: str, expiration_date: date | None = None) -> OptionChain:
        return await call_with_fallback(
            "options.get_options_chain",
            self._attempts("get_options_chain", symbol, expiration_date=expiration_date),
        )

    async def get_options_snapshot(self, symbol: str, **filters: Any) -> DataFrameLike:
        return await call_with_fallback(
            "options.get_options_snapshot", self._attempts("get_options_snapshot", symbol, **filters)
        )
