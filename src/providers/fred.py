import logging
from datetime import date
from typing import Any

import pandas as pd
import polars as pl
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeDataNotFoundError
from finpipe.core.interfaces import IMacroProvider, IProviderDescribe
from finpipe.core.registry import BuildContext, register_provider
from finpipe.network.cache import create_cache_backend
from finpipe.network.resilience import create_resilient_http_client

from finpipe.providers.descriptor import provider_descriptor

logger = logging.getLogger(__name__)


class FredAdapter(IMacroProvider, IProviderDescribe):
    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._provider_config = config.providers.fred
        self._provider_config.ensure_configured()
        self._api_key = self._provider_config.api_key
        self._cache = create_cache_backend(config.cache)
        self._client = create_resilient_http_client(
            "fred", self._provider_config.rate_limits, cache_config=config.cache
        )
        self._base_url = "https://api.stlouisfed.org/fred"

    async def describe(self) -> dict[str, Any]:
        cfg = self._provider_config
        return provider_descriptor(
            provider_id="fred",
            capability="macro",
            provider_config=cfg,
            configured=bool(self._api_key),
            details={"api_base_url": self._base_url},
        )

    async def close(self) -> None:
        await self._client.close()

    def _format_dataframe(self, df: pd.DataFrame) -> pl.DataFrame | pd.DataFrame:
        if df.empty:
            df = pd.DataFrame(columns=["timestamp", "value"])
        if self._config.dataframe_format == "pandas":
            return df
        return pl.from_pandas(df)

    async def get_macro_series(
        self, series_id: str, start_date: date, end_date: date
    ) -> pl.DataFrame | pd.DataFrame:
        cache_key = f"fred_{series_id}_{start_date}_{end_date}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return self._format_dataframe(pd.DataFrame.from_dict(cached))

        params = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "observation_start": start_date.strftime("%Y-%m-%d"),
            "observation_end": end_date.strftime("%Y-%m-%d"),
        }
        response = await self._client.request(
            "GET", f"{self._base_url}/series/observations", params=params
        )
        data = response.json()
        if "observations" not in data:
            raise FinpipeDataNotFoundError(f"Failed to fetch FRED series {series_id}")

        parsed = []
        for obs in data["observations"]:
            val = obs.get("value", ".")
            if val != ".":
                parsed.append({"timestamp": pd.to_datetime(obs["date"]), "value": float(val)})
        df = pd.DataFrame(parsed)
        if not df.empty:
            self._cache.set(
                cache_key, df.to_dict(orient="list"), self._provider_config.ttls.macro_series_sec
            )
        return self._format_dataframe(df)


@register_provider("fred", category="macro")
def build_fred(ctx: BuildContext) -> FredAdapter:
    return FredAdapter(ctx.config)
