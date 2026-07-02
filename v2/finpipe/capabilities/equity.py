"""Typed equity capability service (implements the protocols — full type checking).

Routing semantics:
- providers DISABLED in settings are skipped in the route
- an ENABLED provider with missing credentials raises ``FinpipeConfigError``
  loudly on first use (fix it or disable it — no silent skipping)
- fallback per capabilities/policy.py: on not-found / provider-down / empty,
  never on rate-limit or config errors.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from ..core.models import TickerMetadata
from ..core.protocols import DataFrameLike
from .policy import call_with_fallback

if TYPE_CHECKING:
    from ..core.config import FinpipeConfig
    from ..providers.wiring import AdapterPool


def build_route(config: FinpipeConfig, primary_key: str, fallback_key: str) -> list[str]:
    routing = config.routing.model_dump()
    primary = routing.get(primary_key)
    fallback = routing.get(fallback_key)
    route = [primary] if primary else []
    if fallback and fallback != primary:
        route.append(fallback)
    return route


class EquityService:
    def __init__(self, pool: AdapterPool, config: FinpipeConfig) -> None:
        self._pool = pool
        self._route = build_route(config, "equity_primary", "equity_fallback")

    def _attempts(self, method: str, *args: Any, **kwargs: Any):
        attempts = []
        for name in self._route:
            adapter = self._pool.get_if_enabled(name)
            if adapter is None or not hasattr(adapter, method):
                continue
            attempts.append((name, lambda a=adapter, m=method: getattr(a, m)(*args, **kwargs)))
        return attempts

    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, interval: str = "1d"
    ) -> DataFrameLike:
        return await call_with_fallback(
            "equity.get_historical_prices",
            self._attempts("get_historical_prices", symbol, start_date, end_date, interval),
        )

    async def get_live_spot_price(self, symbol: str) -> float | None:
        return await call_with_fallback(
            "equity.get_live_spot_price", self._attempts("get_live_spot_price", symbol)
        )

    async def get_metadata(self, symbol: str) -> TickerMetadata:
        return await call_with_fallback("equity.get_metadata", self._attempts("get_metadata", symbol))

    async def get_financial_statements(self, symbol: str) -> dict[str, Any]:
        return await call_with_fallback(
            "equity.get_financial_statements", self._attempts("get_financial_statements", symbol)
        )
