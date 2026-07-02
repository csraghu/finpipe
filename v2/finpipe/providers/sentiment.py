"""Market intel adapter: Google News RSS, StockTwits, Reddit.

v2 fixes vs v1:
- Reddit OAuth token lives in an in-memory ``TokenStore`` (v1 persisted it to the
  on-disk fetch cache in plaintext — review §3)
- per-source executors built through ``runtime.executor_factory`` with namespaces
  like ``sentiment.reddit`` whose LEAF names now match the hard-cap table
- uniform degradation contract: per-source failures are logged and skipped;
  aggregate methods always return typed results (documented, consistent)
"""

from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from ..core.config import SentimentConfig, SourceConfig
from ..core.errors import FinpipeError
from ..core.models import NewsArticle, SentimentScore, SocialPost, SocialPostKind
from ..runtime.resilience import RequestExecutor
from ..runtime.tokens import TokenStore
from .base import ProviderAdapter, ProviderRuntime
from .manifest import provider

logger = logging.getLogger(__name__)

_BULL_KEYWORDS = ("call", "calls", "moon", "bull", "long", "buy")
_BEAR_KEYWORDS = ("put", "puts", "bear", "short", "sell", "tank", "drop")
_REDDIT_SUBS = ("wallstreetbets", "stocks", "investing")
_REDDIT_PER_SUB = 5
_STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
_STOCKTWITS_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://stocktwits.com/",
    "Origin": "https://stocktwits.com",
}


class NewsSentimentAdapter(ProviderAdapter):
    def __init__(self, runtime: ProviderRuntime) -> None:
        super().__init__(runtime)
        self._config: SentimentConfig = runtime.config
        self._executors: dict[str, RequestExecutor] = {}
        self._tokens = TokenStore()

    def _source(self, name: str) -> SourceConfig:
        return getattr(self._config.sources, name)

    def _executor_for(self, name: str) -> RequestExecutor | None:
        source = self._source(name)
        if not source.enabled:
            return None
        executor = self._executors.get(name)
        if executor is None:
            assert self._rt.executor_factory is not None
            executor = self._rt.executor_factory(f"sentiment.{name}", source.rate_limits, source.http)
            self._executors[name] = executor
        return executor

    async def close(self) -> None:
        for executor in self._executors.values():
            await executor.close()
        await self._rt.executor.close()

    async def describe(self) -> dict[str, Any]:
        from ..observe.describe import provider_descriptor, settings_snapshot

        sources = {
            name: settings_snapshot(self._source(name))
            for name in ("google_news", "stocktwits", "reddit")
        }
        return provider_descriptor("sentiment", "intel", self._config, details={"sources": sources})

    # -- Google News ---------------------------------------------------------------
    async def _fetch_google_news(self, symbol: str | None, limit: int) -> list[NewsArticle]:
        executor = self._executor_for("google_news")
        if executor is None:
            return []

        async def fetch() -> list[dict[str, Any]]:
            query = symbol or "market news"
            url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
            response = await executor.request("GET", url)
            root = ET.fromstring(response.text)
            articles = []
            for item in root.findall(".//item")[:limit]:
                pub = item.findtext("pubDate")
                try:
                    published = parsedate_to_datetime(pub) if pub else datetime.now()
                except (TypeError, ValueError):
                    published = datetime.now()
                articles.append(
                    NewsArticle(
                        title=item.findtext("title") or "",
                        link=item.findtext("link") or "",
                        published_at=published,
                        publisher="Google News",
                        related_tickers=[symbol] if symbol else [],
                    ).model_dump()
                )
            return articles

        try:
            cached = await self.cached_fetch(
                "google_news", (symbol or "-", limit),
                self._config.source_fetch_ttl("google_news"), fetch,
            )
        except FinpipeError as exc:
            logger.warning("google_news source failed: %s", exc)
            return []
        return [NewsArticle.model_validate(item) for item in cached]

    # -- StockTwits -----------------------------------------------------------------
    async def _stocktwits_stream(self, symbol: str) -> dict[str, Any] | None:
        executor = self._executor_for("stocktwits")
        if executor is None:
            return None
        headers = dict(_STOCKTWITS_HEADERS)
        user_agent = self._source("stocktwits").http.user_agent
        if user_agent:
            headers["User-Agent"] = user_agent
        try:
            response = await executor.request(
                "GET", _STOCKTWITS_URL.format(symbol=symbol), headers=headers
            )
        except FinpipeError as exc:
            logger.warning("stocktwits source failed for %s: %s", symbol, exc)
            return None
        data = response.json()
        return data if isinstance(data, dict) else None

    async def _stocktwits_counts(self, symbol: str) -> tuple[int, int]:
        async def fetch() -> list[int]:
            data = await self._stocktwits_stream(symbol)
            if data is None:
                return [0, 0]
            bullish = bearish = 0
            for msg in data.get("messages", []):
                label = _stocktwits_label(msg)
                if label == "bullish":
                    bullish += 1
                elif label == "bearish":
                    bearish += 1
            return [bullish, bearish]

        counts = await self.cached_fetch(
            "stocktwits_counts", (symbol,), self._config.source_fetch_ttl("stocktwits"), fetch
        )
        return int(counts[0]), int(counts[1])

    async def _stocktwits_posts(self, symbol: str, limit: int) -> list[SocialPost]:
        async def fetch() -> list[dict[str, Any]]:
            data = await self._stocktwits_stream(symbol)
            if data is None:
                return []
            posts = []
            for msg in data.get("messages", [])[:limit]:
                body, user = msg.get("body", ""), msg.get("user", {})
                msg_id = msg.get("id", "")
                if not body or not msg_id:
                    continue
                username = user.get("username", "unknown")
                posts.append(
                    SocialPost(
                        kind=SocialPostKind.MICROBLOG,
                        text=body,
                        url=f"https://stocktwits.com/{username}/message/{msg_id}",
                        author=username,
                    ).model_dump()
                )
            return posts

        cached = await self.cached_fetch(
            "stocktwits_posts", (symbol, limit), self._config.source_fetch_ttl("stocktwits"), fetch
        )
        return [SocialPost.model_validate(p) for p in cached]

    # -- Reddit ------------------------------------------------------------------------
    async def _reddit_token(self, executor: RequestExecutor) -> str | None:
        reddit = self._config.sources.reddit
        if not reddit.client_id or not reddit.client_secret:
            logger.warning("Reddit credentials missing (REDDIT_CLIENT_ID/SECRET); skipping source")
            return None

        async def fetch() -> tuple[str, float]:
            assert reddit.client_id is not None
            assert reddit.client_secret is not None
            response = await executor.request(
                "POST",
                "https://www.reddit.com/api/v1/access_token",
                auth=(reddit.client_id.get_secret_value(), reddit.client_secret.get_secret_value()),
                data={"grant_type": "client_credentials"},
            )
            data = response.json()
            return str(data["access_token"]), float(data.get("expires_in", 3600))

        try:
            return await self._tokens.get_or_fetch("reddit", fetch)
        except (FinpipeError, KeyError) as exc:
            logger.warning("Reddit OAuth failed: %s", exc)
            return None

    async def _reddit_entries(self, symbol: str) -> list[tuple[str, str, str]]:
        executor = self._executor_for("reddit")
        if executor is None:
            return []
        token = await self._reddit_token(executor)
        if token is None:
            return []
        headers = {"Authorization": f"Bearer {token}"}
        entries: list[tuple[str, str, str]] = []
        for subreddit in _REDDIT_SUBS:
            url = (
                f"https://oauth.reddit.com/r/{subreddit}/search.json"
                f"?q={symbol}&restrict_sr=on&sort=new&t=week"
            )
            try:
                response = await executor.request("GET", url, headers=headers)
            except FinpipeError as exc:
                logger.info("Reddit source skipped for %s/%s: %s", subreddit, symbol, exc)
                continue
            for child in response.json().get("data", {}).get("children", [])[:_REDDIT_PER_SUB]:
                cdata = child.get("data", {})
                title = (cdata.get("title") or "").strip()
                permalink = cdata.get("permalink") or ""
                post_url = f"https://www.reddit.com{permalink}" if permalink else (cdata.get("url") or "")
                if title and post_url:
                    entries.append((title, post_url, (cdata.get("selftext") or title).strip()))
        return entries

    async def _reddit_counts(self, symbol: str) -> tuple[int, int]:
        async def fetch() -> list[int]:
            bullish = bearish = 0
            for title, _, _ in await self._reddit_entries(symbol):
                lowered = title.lower()
                if any(word in lowered for word in _BULL_KEYWORDS):
                    bullish += 1
                if any(word in lowered for word in _BEAR_KEYWORDS):
                    bearish += 1
            return [bullish, bearish]

        counts = await self.cached_fetch(
            "reddit_counts", (symbol,), self._config.source_fetch_ttl("reddit"), fetch
        )
        return int(counts[0]), int(counts[1])

    async def _reddit_posts(self, symbol: str, limit: int) -> list[SocialPost]:
        async def fetch() -> list[dict[str, Any]]:
            return [
                SocialPost(kind=SocialPostKind.FORUM, text=body, title=title, url=url).model_dump()
                for title, url, body in (await self._reddit_entries(symbol))[:limit]
            ]

        cached = await self.cached_fetch(
            "reddit_posts", (symbol, limit), self._config.source_fetch_ttl("reddit"), fetch
        )
        return [SocialPost.model_validate(p) for p in cached]

    # -- IMarketIntelProvider ------------------------------------------------------------
    async def get_news(self, symbol: str | None = None, limit: int = 20) -> list[NewsArticle]:
        articles = await self._fetch_google_news(symbol, limit)
        articles.sort(key=lambda a: a.published_at, reverse=True)
        return articles[:limit]

    async def get_social_posts(
        self, symbol: str, *, limit: int = 30, kind: SocialPostKind | None = None
    ) -> list[SocialPost]:
        tasks = []
        if kind in (None, SocialPostKind.MICROBLOG) and self._source("stocktwits").enabled:
            tasks.append(self._stocktwits_posts(symbol, limit))
        if kind in (None, SocialPostKind.FORUM) and self._source("reddit").enabled:
            tasks.append(self._reddit_posts(symbol, limit))
        if not tasks:
            return []
        batches = await asyncio.gather(*tasks)
        posts: list[SocialPost] = [post for batch in batches for post in batch]
        return posts[:limit]

    async def get_sentiment_score(self, symbol: str) -> SentimentScore:
        tasks = []
        sources: list[str] = []
        if self._source("stocktwits").enabled:
            tasks.append(self._stocktwits_counts(symbol))
            sources.append("microblog")
        if self._source("reddit").enabled:
            tasks.append(self._reddit_counts(symbol))
            sources.append("forum")
        results = await asyncio.gather(*tasks) if tasks else []
        bullish = sum(r[0] for r in results)
        bearish = sum(r[1] for r in results)
        total = bullish + bearish
        return SentimentScore(
            symbol=symbol,
            source="+".join(sources) if sources else "none",
            timestamp=datetime.now(),
            score=(bullish - bearish) / total if total else 0.0,
            magnitude=float(total),
        )


def _stocktwits_label(msg: dict[str, Any]) -> str | None:
    legacy = msg.get("sentiment") or {}
    if isinstance(legacy, dict) and isinstance(legacy.get("class"), str):
        return legacy["class"].lower()
    entities = (msg.get("entities") or {}).get("sentiment") or {}
    if isinstance(entities, dict) and isinstance(entities.get("basic"), str):
        return entities["basic"].lower()
    return None


@provider(
    "sentiment",
    capability="intel",
    config_attr="sentiment",
    label="News & Sentiment",
    description="Google News RSS, StockTwits, and Reddit social sentiment",
    probe="intel.sentiment",
)
def build_sentiment(runtime: ProviderRuntime) -> NewsSentimentAdapter:
    return NewsSentimentAdapter(runtime)
