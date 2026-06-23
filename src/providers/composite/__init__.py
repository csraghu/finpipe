"""Composite capability facades with primary/fallback routing."""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from datetime import date
from typing import Any

import pandas as pd
import polars as pl
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.core.models import NewsArticle, OptionChain, SentimentScore, TickerMetadata

logger = logging.getLogger(__name__)


def ordered_provider_names(
    config: FinpipeConfig,
    *,
    primary_key: str,
    fallback_key: str,
) -> list[str]:
    routing = config.routing
    primary = getattr(routing, primary_key)
    fallback = getattr(routing, fallback_key)
    names = [primary]
    if fallback and fallback != primary:
        names.append(fallback)
    return names


async def call_with_fallback(
    adapters: dict[str, Any],
    provider_names: list[str],
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Invoke ``method_name`` on the first provider that succeeds."""
    last_exc: Exception | None = None
    for name in provider_names:
        adapter = adapters.get(name)
        if adapter is None:
            continue
        method = getattr(adapter, method_name, None)
        if method is None or not callable(method):
            continue
        try:
            result = method(*args, **kwargs)
            if isinstance(result, Awaitable):
                return await result
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "Provider %s.%s failed; trying fallback if configured",
                name,
                method_name,
                exc_info=exc,
            )
    if last_exc is not None:
        raise last_exc
    raise FinpipeProviderDownError(
        f"No provider succeeded for {method_name} (candidates: {provider_names})"
    )


def resolve_first_adapter(
    adapters: dict[str, Any],
    provider_names: list[str],
) -> Any | None:
    for name in provider_names:
        adapter = adapters.get(name)
        if adapter is not None:
            return adapter
    return None


class CompositeEquityService:
    """Routes equity I/O via ``routing.equity_primary`` / ``equity_fallback``."""

    def __init__(
        self,
        config: FinpipeConfig,
        *,
        adapters: dict[str, Any],
        options: CompositeOptionsService | None = None,
    ) -> None:
        self._config = config
        self._adapters = adapters
        self._options = options
        self._route = ordered_provider_names(
            config, primary_key="equity_primary", fallback_key="equity_fallback"
        )

    async def get_metadata(self, symbol: str) -> TickerMetadata:
        return await call_with_fallback(
            self._adapters, self._route, "get_metadata", symbol
        )

    async def get_historical_prices(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> pl.DataFrame | pd.DataFrame:
        return await call_with_fallback(
            self._adapters,
            self._route,
            "get_historical_prices",
            symbol,
            start_date,
            end_date,
            interval=interval,
        )

    async def get_live_spot_price(self, symbol: str) -> float | None:
        return await call_with_fallback(
            self._adapters, self._route, "get_live_spot_price", symbol
        )

    async def get_financial_statements(self, symbol: str) -> dict[str, Any]:
        return await call_with_fallback(
            self._adapters, self._route, "get_financial_statements", symbol
        )

    async def get_options_chain(
        self, symbol: str, expiration_date: date | None = None
    ) -> OptionChain:
        if self._options is not None:
            return await self._options.get_options_chain(symbol, expiration_date)
        return await call_with_fallback(
            self._adapters,
            self._route,
            "get_options_chain",
            symbol,
            expiration_date=expiration_date,
        )


class CompositeOptionsService:
    """Routes options I/O via ``routing.options_primary`` / ``options_fallback``."""

    def __init__(self, config: FinpipeConfig, *, adapters: dict[str, Any]) -> None:
        self._config = config
        self._adapters = adapters
        self._route = ordered_provider_names(
            config, primary_key="options_primary", fallback_key="options_fallback"
        )

    @property
    def api_key(self) -> str | None:
        primary = resolve_first_adapter(self._adapters, self._route)
        if primary is None:
            return None
        return getattr(primary, "api_key", None)

    async def fetch_options_contracts(self, symbol: str) -> list[dict[str, Any]]:
        return await call_with_fallback(
            self._adapters, self._route, "fetch_options_contracts", symbol
        )

    async def fetch_options_snapshot(
        self,
        symbol: str,
        expiration_date: str | None = None,
        contract_type: str | None = None,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        limit: int = 250,
        sort: str | None = None,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        return await call_with_fallback(
            self._adapters,
            self._route,
            "fetch_options_snapshot",
            symbol,
            expiration_date=expiration_date,
            contract_type=contract_type,
            strike_price_gte=strike_price_gte,
            strike_price_lte=strike_price_lte,
            limit=limit,
            sort=sort,
            order=order,
        )

    async def fetch_single_option_snapshot(
        self, symbol: str, contract: str
    ) -> dict[str, Any]:
        return await call_with_fallback(
            self._adapters,
            self._route,
            "fetch_single_option_snapshot",
            symbol,
            contract,
        )

    async def fetch_historical_aggs(
        self, symbol: str, from_date: str, to_date: str
    ) -> list[dict[str, Any]]:
        return await call_with_fallback(
            self._adapters,
            self._route,
            "fetch_historical_aggs",
            symbol,
            from_date,
            to_date,
        )

    async def sync_flatfile_from_s3(
        self, remote_key: str, local_dest_path: str
    ) -> bool:
        return await call_with_fallback(
            self._adapters,
            self._route,
            "sync_flatfile_from_s3",
            remote_key,
            local_dest_path,
        )

    async def list_s3_files(self, prefix: str) -> list[dict[str, Any]]:
        return await call_with_fallback(
            self._adapters, self._route, "list_s3_files", prefix
        )

    async def get_options_chain(
        self, symbol: str, expiration_date: date | None = None
    ) -> OptionChain:
        return await call_with_fallback(
            self._adapters,
            self._route,
            "get_options_chain",
            symbol,
            expiration_date=expiration_date,
        )

    async def get_options_snapshot(
        self, symbol: str, **filters: Any
    ) -> pl.DataFrame | pd.DataFrame:
        return await call_with_fallback(
            self._adapters,
            self._route,
            "get_options_snapshot",
            symbol,
            **filters,
        )


class CompositeMacroService:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config


class CompositeIntelService:
    """Routes market intel via the sentiment adapter and configured sources."""

    def __init__(self, config: FinpipeConfig, *, sentiment: Any) -> None:
        self._config = config
        self._sentiment = sentiment

    async def get_news(
        self, symbol: str | None = None, limit: int = 20
    ) -> list[NewsArticle]:
        return await self._sentiment.get_news(symbol, limit=limit)

    async def get_google_news(
        self, symbol: str | None = None, limit: int = 20
    ) -> list[NewsArticle]:
        return await self._sentiment.get_google_news(symbol, limit=limit)

    async def get_reddit_posts(
        self, symbol: str, limit: int = 25
    ) -> list[dict[str, Any]]:
        return await self._sentiment.get_reddit_posts(symbol, limit=limit)

    async def get_stocktwits_messages(
        self, symbol: str, limit: int = 30
    ) -> list[dict[str, Any]]:
        return await self._sentiment.get_stocktwits_messages(symbol, limit=limit)

    async def get_sentiment_score(self, symbol: str) -> SentimentScore:
        return await self._sentiment.get_sentiment_score(symbol)


class CompositeScreenerService:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config


class CompositeLlmService:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config
