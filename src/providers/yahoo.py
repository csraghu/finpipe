import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pandas as pd
import polars as pl
import pybreaker
import yfinance as yf
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.core.interfaces import IHistoricalPriceProvider, IMetadataProvider, IOptionsProvider
from finpipe.core.models import OptionChain, OptionContract, TickerMetadata
from finpipe.core.registry import BuildContext, register_provider
from finpipe.network.cache import create_cache_backend
from finpipe.network.limiter import build_adaptive_limiter
from finpipe.network.resilience import rate_limit_db_path

logger = logging.getLogger(__name__)


class YahooFinanceAdapter(IHistoricalPriceProvider, IMetadataProvider, IOptionsProvider):
    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._rate_limits = config.providers.yahoo.rate_limits
        self._limiter = build_adaptive_limiter(
            "yahoo",
            self._rate_limits,
            db_path=rate_limit_db_path(config.cache),
        )
        self._breaker = pybreaker.CircuitBreaker(
            fail_max=self._rate_limits.circuit_breaker_failure_threshold,
            reset_timeout=self._rate_limits.circuit_breaker_recovery_timeout_sec,
            state_storage=pybreaker.CircuitMemoryStorage(pybreaker.STATE_CLOSED),
        )
        self._cache = create_cache_backend(config.cache)

    async def _execute_with_resilience(
        self, func: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        await self._limiter.acquire()

        async def _run() -> Any:
            async with self._limiter.concurrency.limit():
                result = await asyncio.to_thread(func, *args, **kwargs)
                self._limiter.record_success()
                return result

        circuit_protected = self._breaker(_run)

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._rate_limits.max_retries),
                wait=wait_exponential_jitter(
                    initial=1.0,
                    max=10.0,
                    exp_base=self._rate_limits.backoff_multiplier,
                ),
                retry=retry_if_exception_type(Exception),
                reraise=True,
            ):
                with attempt:
                    return await circuit_protected()
        except pybreaker.CircuitBreakerError as exc:
            logger.error("Yahoo Finance circuit breaker tripped")
            raise FinpipeProviderDownError("Yahoo Finance Circuit breaker tripped") from exc

    def _format_dataframe(self, df: pd.DataFrame) -> pl.DataFrame | pd.DataFrame:
        if df.empty:
            df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        if isinstance(df.index, pd.DatetimeIndex) or df.index.name in ("Date", "Datetime"):
            df = df.reset_index()
            first_col = str(df.columns[0])
            df.rename(columns={first_col: "timestamp"}, inplace=True)

        df.columns = [str(c).lower() for c in df.columns]

        if self._config.dataframe_format == "pandas":
            return df
        return pl.from_pandas(df)

    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, interval: str = "1d"
    ) -> pl.DataFrame | pd.DataFrame:
        cache_key = f"yf_hist_{symbol}_{start_date}_{end_date}_{interval}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return self._format_dataframe(pd.DataFrame.from_dict(cached))

        ticker = yf.Ticker(symbol)
        df = await self._execute_with_resilience(
            ticker.history,
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            interval=interval,
        )
        self._cache.set(
            cache_key,
            df.to_dict(orient="list"),
            self._config.providers.yahoo.ttls.historical_prices_sec,
        )
        return self._format_dataframe(df)

    async def get_live_spot_price(self, symbol: str) -> float | None:
        cache_key = f"yf_spot_{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        ticker = yf.Ticker(symbol)
        info = await self._execute_with_resilience(lambda: ticker.fast_info)
        price = info.get("lastPrice")
        if price is not None:
            self._cache.set(cache_key, price, self._config.providers.yahoo.ttls.live_spot_price_sec)
        return price

    async def get_metadata(self, symbol: str) -> TickerMetadata:
        cache_key = f"yf_meta_{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return TickerMetadata(**cached)

        ticker = yf.Ticker(symbol)
        info = await self._execute_with_resilience(lambda: ticker.info)
        metadata = TickerMetadata(
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
        )
        self._cache.set(
            cache_key, metadata.model_dump(), self._config.providers.yahoo.ttls.metadata_sec
        )
        return metadata

    async def get_financial_statements(self, symbol: str) -> dict[str, Any]:
        cache_key = f"yf_fin_{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        ticker = yf.Ticker(symbol)
        balance_sheet = await self._execute_with_resilience(lambda: ticker.balance_sheet)
        income_stmt = await self._execute_with_resilience(lambda: ticker.income_stmt)
        cash_flow = await self._execute_with_resilience(lambda: ticker.cashflow)
        result = {
            "balance_sheet": balance_sheet.to_dict(),
            "income_statement": income_stmt.to_dict(),
            "cash_flow": cash_flow.to_dict(),
        }
        self._cache.set(
            cache_key, result, self._config.providers.yahoo.ttls.financial_statements_sec
        )
        return result

    async def get_options_chain(
        self, symbol: str, expiration_date: date | None = None
    ) -> OptionChain:
        ticker = yf.Ticker(symbol)

        def _fetch_chain():
            exps = ticker.options
            if not exps:
                return None
            target_exp = expiration_date.strftime("%Y-%m-%d") if expiration_date else exps[0]
            if target_exp not in exps:
                target_exp = exps[0]
            return target_exp, ticker.option_chain(target_exp)

        result = await self._execute_with_resilience(_fetch_chain)
        if not result:
            return OptionChain(symbol=symbol, expiration_date=expiration_date or date.today())

        exp_str, chain = result

        def _parse_contracts(df: pd.DataFrame, in_the_money: bool) -> list[OptionContract]:
            def _safe_get(row, key: str, default=0.0):
                val = row.get(key)
                if pd.isna(val) or val is None:
                    return default
                return val

            return [
                OptionContract(
                    contract_symbol=str(row.get("contractSymbol") or ""),
                    strike=float(_safe_get(row, "strike", 0.0)),
                    last_price=float(_safe_get(row, "lastPrice", 0.0)),
                    bid=float(_safe_get(row, "bid", 0.0)),
                    ask=float(_safe_get(row, "ask", 0.0)),
                    volume=int(_safe_get(row, "volume", 0)),
                    open_interest=int(_safe_get(row, "openInterest", 0)),
                    implied_volatility=float(_safe_get(row, "impliedVolatility", 0.0)),
                    in_the_money=bool(row.get("inTheMoney", in_the_money)),
                )
                for _, row in df.iterrows()
            ]

        exp_dt = datetime.strptime(exp_str, "%Y-%m-%d").date()
        return OptionChain(
            symbol=symbol,
            expiration_date=exp_dt,
            calls=_parse_contracts(chain.calls, True),
            puts=_parse_contracts(chain.puts, False),
        )

    async def get_options_snapshot(self, symbol: str, **filters) -> pl.DataFrame | pd.DataFrame:
        chain = await self.get_options_chain(symbol)
        data = []
        for call in chain.calls:
            row = call.model_dump()
            row["type"] = "CALL"
            data.append(row)
        for put in chain.puts:
            row = put.model_dump()
            row["type"] = "PUT"
            data.append(row)
        return self._format_dataframe(pd.DataFrame(data))


@register_provider("yahoo", category="equity")
def build_yahoo(ctx: BuildContext) -> YahooFinanceAdapter:
    return YahooFinanceAdapter(ctx.config)
