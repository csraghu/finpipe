"""Capability protocols and type aliases (v1 intent kept; async-only)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Protocol, runtime_checkable

import pandas as pd
import polars as pl

from .models import (
    LLMResponse,
    NewsArticle,
    OptionChain,
    SentimentScore,
    SocialPost,
    SocialPostKind,
    TickerMetadata,
)

Interval = Literal["1m", "5m", "15m", "1h", "1d", "1wk", "1mo"]
DataFrameLike = pl.DataFrame | pd.DataFrame


@runtime_checkable
class IHistoricalPriceProvider(Protocol):
    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, interval: str = "1d"
    ) -> DataFrameLike: ...

    async def get_live_spot_price(self, symbol: str) -> float | None: ...


@runtime_checkable
class IMetadataProvider(Protocol):
    async def get_metadata(self, symbol: str) -> TickerMetadata: ...

    async def get_financial_statements(self, symbol: str) -> dict[str, Any]: ...


@runtime_checkable
class IOptionsProvider(Protocol):
    async def get_options_chain(
        self, symbol: str, expiration_date: date | None = None
    ) -> OptionChain: ...

    async def get_options_snapshot(self, symbol: str, **filters: Any) -> DataFrameLike: ...


@runtime_checkable
class IMacroProvider(Protocol):
    async def get_macro_series(
        self, series_id: str, start_date: date, end_date: date
    ) -> DataFrameLike: ...


@runtime_checkable
class IMarketIntelProvider(Protocol):
    async def get_news(self, symbol: str | None = None, limit: int = 20) -> list[NewsArticle]: ...

    async def get_social_posts(
        self, symbol: str, *, limit: int = 30, kind: SocialPostKind | None = None
    ) -> list[SocialPost]: ...

    async def get_sentiment_score(self, symbol: str) -> SentimentScore: ...


@runtime_checkable
class IScreenerProvider(Protocol):
    async def run_screener(self, criteria: dict[str, Any]) -> list[str]: ...


@runtime_checkable
class ILLMProvider(Protocol):
    async def generate_response(
        self, prompt: str, model: str | None = None, **kwargs: Any
    ) -> LLMResponse: ...


@runtime_checkable
class IProviderDescribe(Protocol):
    async def describe(self) -> dict[str, Any]: ...


@runtime_checkable
class ICloseable(Protocol):
    async def close(self) -> None: ...
