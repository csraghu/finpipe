"""Typed screener capability service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.config import FinpipeConfig
    from ..providers.wiring import AdapterPool


class ScreenerService:
    def __init__(self, pool: AdapterPool, config: FinpipeConfig) -> None:
        self._pool = pool

    async def run(self, source: str, **params: Any) -> list[str]:
        return await self._pool.get("screener").run(source, **params)

    async def get_trending(self) -> list[str]:
        return await self._pool.get("screener").get_trending()

    async def get_predefined(self, scr_id: str, *, limit: int | None = None) -> list[str]:
        return await self._pool.get("screener").get_predefined(scr_id, limit=limit)

    async def get_fundamental(self, filter_key: str) -> list[str]:
        return await self._pool.get("screener").get_fundamental(filter_key)

    async def run_tradingview(self, criteria: dict[str, Any]) -> list[str]:
        return await self._pool.get("screener").run_tradingview(criteria)
