import logging
from datetime import date
from io import StringIO
from typing import Any

import pandas as pd
import polars as pl
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeDataNotFoundError, FinpipeRateLimitExceededError
from finpipe.core.interfaces import IHistoricalPriceProvider, IMetadataProvider, IProviderDescribe
from finpipe.core.models import TickerMetadata
from finpipe.core.registry import BuildContext, register_provider
from finpipe.network.cache_manager import resolve_cache_backend
from finpipe.network.resilience import create_resilient_http_client
from finpipe.providers.descriptor import provider_descriptor

logger = logging.getLogger(__name__)


class AlphaVantageAdapter(IHistoricalPriceProvider, IMetadataProvider, IProviderDescribe):
    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._provider_config = config.providers.alpha_vantage
        self._provider_config.ensure_configured()
        self._api_key = self._provider_config.api_key
        self._cache = resolve_cache_backend(config.cache)
        self._client = create_resilient_http_client(
            "alpha_vantage", self._provider_config.rate_limits, cache_config=config.cache
        )
        self._base_url = "https://www.alphavantage.co/query"

    async def describe(self) -> dict[str, Any]:
        cfg = self._provider_config
        return provider_descriptor(
            provider_id="alpha_vantage",
            capability="equity",
            provider_config=cfg,
            configured=bool(self._api_key),
            details={"api_base_url": self._base_url},
        )

    async def close(self) -> None:
        await self._client.close()

    def _check_rate_limit(self, data: Any) -> None:
        if isinstance(data, dict):
            info = data.get("Information", "")
            if "rate limit" in info.lower() or "frequency" in info.lower():
                raise FinpipeRateLimitExceededError("Alpha Vantage rate limit exceeded")
        elif isinstance(data, str):
            if "Information" in data and ("rate limit" in data.lower() or "frequency" in data.lower()):
                raise FinpipeRateLimitExceededError("Alpha Vantage rate limit exceeded")

    def _format_dataframe(self, df: pd.DataFrame) -> pl.DataFrame | pd.DataFrame:
        if df.empty:
            df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        if df.index.name == "timestamp" or "timestamp" not in df.columns:
            df = df.reset_index()
        df.columns = [str(c).lower() for c in df.columns]
        if self._config.dataframe_format == "pandas":
            return df
        return pl.from_pandas(df)

    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, interval: str = "1d"
    ) -> pl.DataFrame | pd.DataFrame:
        cache_key = f"av_hist_{symbol}_{interval}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            df = pd.DataFrame.from_dict(cached)
        else:
            function = "TIME_SERIES_DAILY" if interval == "1d" else "TIME_SERIES_INTRADAY"
            params = {
                "function": function,
                "symbol": symbol,
                "apikey": self._api_key,
                "datatype": "csv",
                "outputsize": "compact",
            }
            if interval != "1d":
                params["interval"] = interval
            response = await self._client.request("GET", self._base_url, params=params)
            self._check_rate_limit(response.text)
            if "Error Message" in response.text or "Invalid API call" in response.text:
                raise FinpipeDataNotFoundError(
                    f"Alpha Vantage returned an error or invalid ticker for {symbol}"
                )
            df = pd.read_csv(StringIO(response.text))
            if "timestamp" not in df.columns:
                raise FinpipeDataNotFoundError(
                    f"Alpha Vantage returned unexpected format for {symbol}: {response.text[:150]}"
                )
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df = df.sort_values("timestamp")
            self._cache.set(
                cache_key,
                df.to_dict(orient="list"),
                self._provider_config.ttls.historical_prices_sec,
            )

        days = pd.to_datetime(df["timestamp"]).dt.normalize()
        mask = (days >= pd.Timestamp(start_date)) & (days <= pd.Timestamp(end_date))
        return self._format_dataframe(df.loc[mask])

    async def get_live_spot_price(self, symbol: str) -> float | None:
        cache_key = f"av_spot_{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        params = {"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": self._api_key}
        response = await self._client.request("GET", self._base_url, params=params)
        data = response.json()
        self._check_rate_limit(data)
        quote = data.get("Global Quote", {})
        price_str = quote.get("05. price")
        if not price_str:
            return None
        price = float(price_str)
        self._cache.set(cache_key, price, self._provider_config.ttls.live_spot_price_sec)
        return price

    async def get_metadata(self, symbol: str) -> TickerMetadata:
        cache_key = f"av_meta_{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return TickerMetadata(**cached)
            
        params = {"function": "OVERVIEW", "symbol": symbol, "apikey": self._api_key}
        response = await self._client.request("GET", self._base_url, params=params)
        data = response.json()
        self._check_rate_limit(data)
        
        if not data or "Symbol" not in data:
            params_etf = {"function": "ETF_PROFILE", "symbol": symbol, "apikey": self._api_key}
            response_etf = await self._client.request("GET", self._base_url, params=params_etf)
            data_etf = response_etf.json()
            self._check_rate_limit(data_etf)
            if not data_etf or "symbol" not in data_etf:
                raise FinpipeDataNotFoundError(f"Alpha Vantage metadata not found for {symbol} (tried OVERVIEW and ETF_PROFILE)")
            
            try:
                mcap = float(data_etf.get("net_assets", 0))
            except ValueError:
                mcap = None
                
            metadata = TickerMetadata(
                symbol=data_etf.get("symbol", symbol),
                short_name=data_etf.get("name"),
                long_name=data_etf.get("name"),
                sector="ETF",
                industry="ETF",
                market_cap=mcap,
                exchange=data_etf.get("exchange", "Unknown"),
                currency="USD",
                description=data_etf.get("description"),
            )
        else:
            try:
                mcap = float(data.get("MarketCapitalization", 0))
            except ValueError:
                mcap = None
            metadata = TickerMetadata(
                symbol=data.get("Symbol", symbol),
                short_name=data.get("Name"),
                long_name=data.get("Name"),
                sector=data.get("Sector"),
                industry=data.get("Industry"),
                market_cap=mcap,
                exchange=data.get("Exchange"),
                currency=data.get("Currency"),
                description=data.get("Description"),
            )
            
        self._cache.set(cache_key, metadata.model_dump(), self._provider_config.ttls.metadata_sec)
        return metadata

    async def get_financial_statements(self, symbol: str) -> dict[str, Any]:
        raise NotImplementedError(
            "AlphaVantage financial statements are premium or rate-heavy. Use yfinance for this."
        )


@register_provider("alpha_vantage", category="equity")
def build_alpha_vantage(ctx: BuildContext) -> AlphaVantageAdapter:
    return AlphaVantageAdapter(ctx.config)
