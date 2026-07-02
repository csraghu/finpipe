"""Typed macro capability service (single provider today: FRED)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ..core.protocols import DataFrameLike

if TYPE_CHECKING:
    from ..core.config import FinpipeConfig
    from ..providers.wiring import AdapterPool


class MacroService:
    def __init__(self, pool: AdapterPool, config: FinpipeConfig) -> None:
        self._pool = pool

    async def get_macro_series(
        self, series_id: str, start_date: date, end_date: date
    ) -> DataFrameLike:
        return await self._pool.get("fred").get_macro_series(series_id, start_date, end_date)

    async def get_risk_free_rate(self, *, series_id: str = "DGS10") -> float:
        from datetime import timedelta

        frame = await self.get_macro_series(series_id, date.today() - timedelta(days=14), date.today())
        values = list(frame["value"]) if "value" in getattr(frame, "columns", []) else []
        if not values:
            from ..core.errors import FinpipeDataNotFoundError

            raise FinpipeDataNotFoundError(f"No recent observations for {series_id}")
        return float(values[-1]) / 100.0
