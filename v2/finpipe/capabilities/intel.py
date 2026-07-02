"""Typed market-intel capability service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.models import NewsArticle, SentimentScore, SocialPost, SocialPostKind

if TYPE_CHECKING:
    from ..core.config import FinpipeConfig
    from ..providers.wiring import AdapterPool


class IntelService:
    def __init__(self, pool: AdapterPool, config: FinpipeConfig) -> None:
        self._pool = pool

    async def get_news(self, symbol: str | None = None, limit: int = 20) -> list[NewsArticle]:
        return await self._pool.get("sentiment").get_news(symbol, limit=limit)

    async def get_social_posts(
        self, symbol: str, *, limit: int = 30, kind: SocialPostKind | None = None
    ) -> list[SocialPost]:
        return await self._pool.get("sentiment").get_social_posts(symbol, limit=limit, kind=kind)

    async def get_sentiment_score(self, symbol: str) -> SentimentScore:
        return await self._pool.get("sentiment").get_sentiment_score(symbol)
