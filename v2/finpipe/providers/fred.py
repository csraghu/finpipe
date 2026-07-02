"""FRED macro adapter — the v2 reference implementation.

Demonstrates every pattern the plan mandates:
- narrow DI: sees only ``FredConfig`` via ``ProviderRuntime`` (no FinpipeConfig)
- zero-I/O constructor; credential check on first use, not at Client()
- API key in request params only, never in the URL path; executor sanitizes logs
- caches NORMALIZED records via ``cached_fetch`` (fresh == cached by construction)
- raises only classified errors; parse problems are ``FinpipeParseError``
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
import polars as pl

from ..core.config import FredConfig
from ..core.errors import FinpipeConfigError, FinpipeParseError
from .base import ProviderAdapter, ProviderRuntime
from .manifest import provider
from .normalize import macro_frame

_BASE_URL = "https://api.stlouisfed.org/fred"


class FredAdapter(ProviderAdapter):
    def __init__(self, runtime: ProviderRuntime) -> None:
        super().__init__(runtime)
        self._config: FredConfig = runtime.config

    def _ensure_configured(self) -> None:
        if self._config.api_key is None:
            raise FinpipeConfigError(
                "FRED requires FRED_API_KEY (or providers.fred.api_key); "
                "set the env var or disable providers.fred"
            )
        super()._ensure_configured()

    async def get_macro_series(
        self, series_id: str, start_date: date, end_date: date
    ) -> pl.DataFrame | pd.DataFrame:
        records = await self.cached_fetch(
            endpoint="macro_series",
            params=(series_id, start_date.isoformat(), end_date.isoformat()),
            ttl_s=self._config.ttls.macro_series_sec,
            fetch=lambda: self._fetch_series(series_id, start_date, end_date),
        )
        return macro_frame(records, self._rt.dataframe_format)

    async def _fetch_series(
        self, series_id: str, start_date: date, end_date: date
    ) -> list[dict[str, Any]]:
        assert self._config.api_key is not None  # guaranteed by _ensure_configured
        response = await self._rt.executor.request(
            "GET",
            f"{_BASE_URL}/series/observations",
            params={
                "series_id": series_id,
                "api_key": self._config.api_key.get_secret_value(),
                "file_type": "json",
                "observation_start": start_date.isoformat(),
                "observation_end": end_date.isoformat(),
            },
        )
        payload = response.json()
        observations = payload.get("observations")
        if observations is None:
            raise FinpipeParseError(f"FRED payload for {series_id} missing 'observations'")
        records: list[dict[str, Any]] = []
        for obs in observations:
            raw_value = obs.get("value", ".")
            if raw_value == ".":  # FRED's missing-data marker
                continue
            records.append(
                {"timestamp": datetime.fromisoformat(obs["date"]), "value": float(raw_value)}
            )
        return records


@provider(
    "fred",
    capability="macro",
    config_attr="fred",
    label="FRED",
    description="Federal Reserve (St. Louis) macroeconomic series",
    secrets=("FRED_API_KEY",),
    probe="macro.fred",
)
def build_fred(runtime: ProviderRuntime) -> FredAdapter:
    return FredAdapter(runtime)
