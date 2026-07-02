"""Alpha Vantage adapter.

v2 fixes vs v1:
- ``outputsize`` chosen from the requested date range (v1 always used ``compact``
  → silently truncated history older than ~100 trading days), and it's part of
  the cache key (v1's key ignored the range entirely)
- HTTP-200 "soft" rate-limit payloads feed AIMD via ``executor.note_rate_limited()``
  and raise ``FinpipeRateLimitExceededError`` (stale-cache degradation applies)
- caches normalized records → cache hit schema == fresh schema
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from io import StringIO
from typing import Any

import pandas as pd

from ..core.config import AlphaVantageConfig
from ..core.errors import (
    FinpipeConfigError,
    FinpipeDataNotFoundError,
    FinpipeRateLimitExceededError,
)
from ..core.models import TickerMetadata
from ..core.protocols import DataFrameLike
from .base import ProviderAdapter, ProviderRuntime
from .manifest import provider
from .normalize import ohlcv_frame

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.alphavantage.co/query"
_COMPACT_WINDOW_DAYS = 100  # AV 'compact' returns ~100 most recent rows


class AlphaVantageAdapter(ProviderAdapter):
    def __init__(self, runtime: ProviderRuntime) -> None:
        super().__init__(runtime)
        self._config: AlphaVantageConfig = runtime.config

    def _ensure_configured(self) -> None:
        if self._config.api_key is None:
            raise FinpipeConfigError(
                "Alpha Vantage requires ALPHA_VANTAGE_API_KEY; set it or disable providers.alpha_vantage"
            )
        super()._ensure_configured()

    def _key(self) -> str:
        assert self._config.api_key is not None
        return self._config.api_key.get_secret_value()

    def _check_soft_rate_limit(self, text_or_json: Any) -> None:
        blob = str(text_or_json)
        if "Information" in blob and ("rate limit" in blob.lower() or "frequency" in blob.lower()):
            self._rt.executor.note_rate_limited()
            raise FinpipeRateLimitExceededError("Alpha Vantage soft rate limit (HTTP 200 Information payload)")

    async def describe(self) -> dict[str, Any]:
        from ..observe.describe import provider_descriptor

        return provider_descriptor(
            "alpha_vantage", "equity", self._config,
            configured=self._config.api_key is not None,
            details={"api_base_url": _BASE_URL},
        )

    # -- IHistoricalPriceProvider -------------------------------------------------
    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, interval: str = "1d"
    ) -> DataFrameLike:
        needs_full = start_date < (date.today() - timedelta(days=_COMPACT_WINDOW_DAYS))
        outputsize = "full" if needs_full else "compact"

        async def fetch() -> list[dict[str, Any]]:
            function = "TIME_SERIES_DAILY" if interval == "1d" else "TIME_SERIES_INTRADAY"
            params: dict[str, Any] = {
                "function": function,
                "symbol": symbol,
                "apikey": self._key(),
                "datatype": "csv",
                "outputsize": outputsize,
            }
            if interval != "1d":
                params["interval"] = interval
            response = await self._rt.executor.request("GET", _BASE_URL, params=params)
            text = response.text
            self._check_soft_rate_limit(text)
            if "Error Message" in text or "Invalid API call" in text:
                raise FinpipeDataNotFoundError(f"Alpha Vantage error / invalid ticker for {symbol}")
            df = pd.read_csv(StringIO(text))
            if "timestamp" not in df.columns:
                raise FinpipeDataNotFoundError(
                    f"Alpha Vantage unexpected payload for {symbol}: {text[:120]}"
                )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values("timestamp")
            return df.to_dict(orient="records")

        records = await self.cached_fetch(
            "historical_prices",
            (symbol, interval, outputsize),
            self._config.ttls.historical_prices_sec,
            fetch,
        )
        start_ts, end_ts = pd.Timestamp(start_date), pd.Timestamp(end_date)
        in_range = [
            r for r in records
            if start_ts <= pd.Timestamp(r["timestamp"]).normalize() <= end_ts
        ]
        return ohlcv_frame(in_range, self._rt.dataframe_format)

    async def get_live_spot_price(self, symbol: str) -> float | None:
        async def fetch() -> float | None:
            response = await self._rt.executor.request(
                "GET", _BASE_URL,
                params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": self._key()},
            )
            data = response.json()
            self._check_soft_rate_limit(data)
            price = (data.get("Global Quote") or {}).get("05. price")
            return float(price) if price else None

        return await self.cached_fetch(
            "live_spot_price", (symbol,), self._config.ttls.live_spot_price_sec, fetch
        )

    # -- IMetadataProvider ------------------------------------------------------------
    async def get_metadata(self, symbol: str) -> TickerMetadata:
        async def fetch() -> dict[str, Any]:
            response = await self._rt.executor.request(
                "GET", _BASE_URL,
                params={"function": "OVERVIEW", "symbol": symbol, "apikey": self._key()},
            )
            data = response.json()
            self._check_soft_rate_limit(data)
            if data and "Symbol" in data:
                return _metadata_from_overview(symbol, data).model_dump()

            etf_response = await self._rt.executor.request(
                "GET", _BASE_URL,
                params={"function": "ETF_PROFILE", "symbol": symbol, "apikey": self._key()},
            )
            etf = etf_response.json()
            self._check_soft_rate_limit(etf)
            if not etf or "symbol" not in etf:
                raise FinpipeDataNotFoundError(
                    f"Alpha Vantage metadata not found for {symbol} (tried OVERVIEW and ETF_PROFILE)"
                )
            return _metadata_from_etf(symbol, etf).model_dump()

        cached = await self.cached_fetch("metadata", (symbol,), self._config.ttls.metadata_sec, fetch)
        return TickerMetadata.model_validate(cached)

    async def get_financial_statements(self, symbol: str) -> dict[str, Any]:
        raise FinpipeDataNotFoundError(
            "Alpha Vantage financial statements are premium/rate-heavy; route equity_primary=yahoo for this call"
        )


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _metadata_from_overview(symbol: str, data: dict[str, Any]) -> TickerMetadata:
    return TickerMetadata(
        symbol=data.get("Symbol", symbol),
        short_name=data.get("Name"),
        long_name=data.get("Name"),
        sector=data.get("Sector"),
        industry=data.get("Industry"),
        market_cap=_safe_float(data.get("MarketCapitalization")),
        exchange=data.get("Exchange"),
        currency=data.get("Currency"),
        description=data.get("Description"),
    )


def _metadata_from_etf(symbol: str, data: dict[str, Any]) -> TickerMetadata:
    return TickerMetadata(
        symbol=data.get("symbol", symbol),
        short_name=data.get("name"),
        long_name=data.get("name"),
        sector="ETF",
        industry="ETF",
        market_cap=_safe_float(data.get("net_assets")),
        exchange=data.get("exchange", "Unknown"),
        currency="USD",
        description=data.get("description"),
    )


@provider(
    "alpha_vantage",
    capability="equity",
    config_attr="alpha_vantage",
    label="Alpha Vantage",
    description="Equity OHLCV, quotes, and company/ETF metadata (keyed REST)",
    secrets=("ALPHA_VANTAGE_API_KEY",),
    probe="equity.alpha_vantage",
)
def build_alpha_vantage(runtime: ProviderRuntime) -> AlphaVantageAdapter:
    return AlphaVantageAdapter(runtime)
