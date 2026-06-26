from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SocialPostKind(StrEnum):
    """Abstract social channel kinds (not vendor names)."""

    FORUM = "forum"
    MICROBLOG = "microblog"


class TickerMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    short_name: str | None = None
    long_name: str | None = None
    sector: str | None = None
    industry: str | None = None
    market_cap: float | None = None
    exchange: str | None = None
    currency: str | None = None
    website: str | None = None
    description: str | None = None


class OptionContract(BaseModel):
    model_config = ConfigDict(extra="allow")

    contract_symbol: str
    strike: float
    last_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None
    in_the_money: bool


class OptionChain(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str
    expiration_date: date
    calls: list[OptionContract] = Field(default_factory=list)
    puts: list[OptionContract] = Field(default_factory=list)


class NewsArticle(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    link: str
    published_at: datetime
    publisher: str | None = None
    summary: str | None = None
    related_tickers: list[str] = Field(default_factory=list)


class SocialPost(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: SocialPostKind
    text: str
    url: str
    title: str | None = None
    author: str | None = None
    created_at: datetime | None = None


class SentimentScore(BaseModel):
    model_config = ConfigDict(extra="allow")

    symbol: str | None = None
    source: str
    timestamp: datetime
    score: float = Field(..., description="Sentiment score typically between -1.0 and 1.0")
    magnitude: float | None = Field(None, description="Volume or intensity of sentiment")


class LLMResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    model_name: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    content: str
    raw_response: Any | None = None
