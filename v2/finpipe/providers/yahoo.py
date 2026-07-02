"""Yahoo Finance adapter (yfinance via sync bridge).

v2 fixes vs v1:
- caches NORMALIZED records, so cache hits have the same schema as fresh fetches
  (v1 dropped the timestamp column on cache hits — review §2.3)
- retries only classified errors via ``executor.execute`` (v1 retried every
  ``Exception`` including "ticker does not exist")
- ``yfinance`` imported lazily inside methods — base install doesn't need the extra
- no fake Massive-compat stubs (v1 had ``sync_flatfile_from_s3 -> False`` etc.)
- options chain honors its configured TTL (v1 never cached it)
"""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..core.config import YahooConfig
from ..core.errors import FinpipeDataNotFoundError
from ..core.models import OptionChain, OptionContract, TickerMetadata
from ..core.protocols import DataFrameLike
from .base import ProviderAdapter, ProviderRuntime
from .manifest import provider
from .normalize import OHLCV_COLUMNS, ohlcv_frame
from .snapshots import chain_to_snapshot_frame

logger = logging.getLogger(__name__)


def _sanitize_symbol(symbol: str) -> str:
    return symbol.replace("/", "-").replace(".", "-")


def _clean(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    return value


class YahooFinanceAdapter(ProviderAdapter):
    def __init__(self, runtime: ProviderRuntime) -> None:
        super().__init__(runtime)
        self._config: YahooConfig = runtime.config

    def _ensure_configured(self) -> None:
        import importlib.util

        if importlib.util.find_spec("yfinance") is None:
            from ..core.errors import FinpipeConfigError

            raise FinpipeConfigError("Yahoo provider requires the 'yfinance' extra: pip install finpipe[yahoo]")
        super()._ensure_configured()

    def _ticker(self, symbol: str) -> Any:
        import yfinance as yf  # lazy: optional extra

        return yf.Ticker(_sanitize_symbol(symbol))

    async def _bridge(self, fn: Any) -> Any:
        """Run a blocking yfinance call under the executor's limits/breaker/retry."""
        return await self._rt.executor.execute(lambda: asyncio.to_thread(fn), context="yfinance")

    async def describe(self) -> dict[str, Any]:
        from ..observe.describe import provider_descriptor

        return provider_descriptor("yahoo", ["equity", "options"], self._config, details={"backend": "yfinance"})

    # -- IHistoricalPriceProvider ------------------------------------------------
    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, interval: str = "1d"
    ) -> DataFrameLike:
        async def fetch() -> list[dict[str, Any]]:
            ticker = self._ticker(symbol)
            df: pd.DataFrame = await self._bridge(
                lambda: ticker.history(
                    start=start_date.isoformat(), end=end_date.isoformat(), interval=interval
                )
            )
            return _yf_history_to_records(df)

        records = await self.cached_fetch(
            "historical_prices",
            (symbol, start_date.isoformat(), end_date.isoformat(), interval),
            self._config.ttls.historical_prices_sec,
            fetch,
        )
        return ohlcv_frame(records, self._rt.dataframe_format)

    async def get_live_spot_price(self, symbol: str) -> float | None:
        async def fetch() -> float | None:
            ticker = self._ticker(symbol)
            info = await self._bridge(lambda: ticker.fast_info)
            price = info.get("lastPrice") if hasattr(info, "get") else getattr(info, "last_price", None)
            return float(price) if price is not None else None

        return await self.cached_fetch(
            "live_spot_price", (symbol,), self._config.ttls.live_spot_price_sec, fetch
        )

    # -- IMetadataProvider ----------------------------------------------------------
    async def get_metadata(self, symbol: str) -> TickerMetadata:
        async def fetch() -> dict[str, Any]:
            ticker = self._ticker(symbol)
            info: dict[str, Any] = await self._bridge(lambda: ticker.info)
            return TickerMetadata(
                symbol=symbol,
                short_name=info.get("shortName"),
                long_name=info.get("longName"),
                sector=info.get("sector"),
                industry=info.get("industry"),
                market_cap=info.get("marketCap"),
                exchange=info.get("exchange"),
                currency=info.get("currency"),
                website=info.get("website"),
                description=info.get("longBusinessSummary"),
            ).model_dump()

        cached = await self.cached_fetch("metadata", (symbol,), self._config.ttls.metadata_sec, fetch)
        return TickerMetadata.model_validate(cached)

    async def get_financial_statements(self, symbol: str) -> dict[str, Any]:
        async def fetch() -> dict[str, Any]:
            ticker = self._ticker(symbol)
            balance = await self._bridge(lambda: ticker.balance_sheet)
            income = await self._bridge(lambda: ticker.income_stmt)
            cash = await self._bridge(lambda: ticker.cashflow)
            return {
                "balance_sheet": _statement_records(balance),
                "income_statement": _statement_records(income),
                "cash_flow": _statement_records(cash),
            }

        return await self.cached_fetch(
            "financial_statements", (symbol,), self._config.ttls.financial_statements_sec, fetch
        )

    # -- IOptionsProvider --------------------------------------------------------------
    async def get_options_chain(self, symbol: str, expiration_date: date | None = None) -> OptionChain:
        async def fetch() -> dict[str, Any]:
            ticker = self._ticker(symbol)

            def _load() -> tuple[str, Any] | None:
                expirations = ticker.options
                if not expirations:
                    return None
                target = expiration_date.isoformat() if expiration_date else expirations[0]
                if target not in expirations:
                    target = expirations[0]
                return target, ticker.option_chain(target)

            result = await self._bridge(_load)
            if result is None:
                raise FinpipeDataNotFoundError(f"No option expirations listed for {symbol}")
            exp_str, chain = result
            return OptionChain(
                symbol=symbol,
                expiration_date=datetime.strptime(exp_str, "%Y-%m-%d").date(),
                calls=_parse_contracts(chain.calls),
                puts=_parse_contracts(chain.puts),
            ).model_dump()

        cached = await self.cached_fetch(
            "options_chain",
            (symbol, expiration_date.isoformat() if expiration_date else "front"),
            self._config.ttls.options_chain_sec,
            fetch,
        )
        return OptionChain.model_validate(cached)

    async def get_options_snapshot(self, symbol: str, **filters: Any) -> DataFrameLike:
        chain = await self.get_options_chain(symbol, filters.get("expiration_date"))
        return chain_to_snapshot_frame(chain, self._rt.dataframe_format)


def _yf_history_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    frame = df.reset_index()
    frame.columns = [str(col).lower() for col in frame.columns]
    if "date" in frame.columns:
        frame = frame.rename(columns={"date": "timestamp"})
    elif "datetime" in frame.columns:
        frame = frame.rename(columns={"datetime": "timestamp"})
    keep = [col for col in OHLCV_COLUMNS if col in frame.columns]
    return frame[keep].to_dict(orient="records")


def _statement_records(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {}
    out: dict[str, Any] = {}
    for col in df.columns:
        key = col.isoformat() if hasattr(col, "isoformat") else str(col)
        series = df[col]
        out[key] = {str(idx): _clean(val) for idx, val in series.items()}
    return out


def _parse_contracts(df: pd.DataFrame) -> list[OptionContract]:
    if df is None or df.empty:
        return []
    contracts: list[OptionContract] = []
    for _, row in df.iterrows():
        contracts.append(
            OptionContract(
                contract_symbol=str(row.get("contractSymbol") or ""),
                strike=float(_clean(row.get("strike"), 0.0)),
                last_price=_clean(row.get("lastPrice")),
                bid=_clean(row.get("bid")),
                ask=_clean(row.get("ask")),
                volume=int(_clean(row.get("volume"), 0)),
                open_interest=int(_clean(row.get("openInterest"), 0)),
                implied_volatility=_clean(row.get("impliedVolatility")),
                in_the_money=bool(_clean(row.get("inTheMoney"), False)),
            )
        )
    return contracts


@provider(
    "yahoo",
    capability="equity",
    config_attr="yahoo",
    label="Yahoo Finance",
    description="Equity OHLCV, metadata, financials, and options via yfinance",
    extra="yahoo",
    probe="equity.yahoo",
)
def build_yahoo(runtime: ProviderRuntime) -> YahooFinanceAdapter:
    return YahooFinanceAdapter(runtime)
