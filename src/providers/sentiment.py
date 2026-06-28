import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from finpipe.core.config import FinpipeConfig, SentimentSourceConfig
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.core.interfaces import IMarketIntelProvider, IProviderDescribe
from finpipe.core.models import NewsArticle, SentimentScore, SocialPost, SocialPostKind
from finpipe.core.registry import BuildContext, register_provider
from finpipe.network.cache_manager import resolve_cache_backend
from finpipe.network.resilience import ResilientHttpClient, create_resilient_http_client
from finpipe.providers.descriptor import provider_descriptor, settings_snapshot

logger = logging.getLogger(__name__)

_REDDIT_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_REDDIT_BULL_KEYWORDS = ("call", "calls", "moon", "bull", "long", "buy")
_REDDIT_BEAR_KEYWORDS = ("put", "puts", "bear", "short", "sell", "tank", "drop")
_REDDIT_FORUM_SUBREDDITS = ("wallstreetbets", "stocks", "investing")
_REDDIT_ENTRIES_PER_SUBREDDIT = 5

_STOCKTWITS_STREAM_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
_STOCKTWITS_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://stocktwits.com/",
    "Origin": "https://stocktwits.com",
}


def _reddit_search_url(symbol: str, subreddit: str) -> str:
    return (
        f"https://www.reddit.com/r/{subreddit}/search.rss?q={symbol}&restrict_sr=on&sort=new&t=week"
    )


def _stocktwits_message_sentiment(msg: dict[str, Any]) -> str | None:
    """Return bullish/bearish label from Stocktwits message (aksh + entities shapes)."""
    legacy = msg.get("sentiment") or {}
    if isinstance(legacy, dict):
        label = legacy.get("class")
        if isinstance(label, str):
            return label.lower()
    entities = (msg.get("entities") or {}).get("sentiment") or {}
    if isinstance(entities, dict):
        basic = entities.get("basic")
        if isinstance(basic, str):
            return basic.lower()
    return None


def _parse_reddit_atom_feed(text: str) -> list[tuple[str, str, str]]:
    """Parse Reddit Atom search feed into (title, url, body) tuples."""
    root = ET.fromstring(text)
    items: list[tuple[str, str, str]] = []
    for entry in root.findall("atom:entry", _REDDIT_ATOM_NS):
        title = (entry.findtext("atom:title", default="", namespaces=_REDDIT_ATOM_NS) or "").strip()
        link_el = entry.find("atom:link", _REDDIT_ATOM_NS)
        url = link_el.get("href", "") if link_el is not None else ""
        body = (
            entry.findtext("atom:content", default="", namespaces=_REDDIT_ATOM_NS)
            or entry.findtext("atom:summary", default="", namespaces=_REDDIT_ATOM_NS)
            or title
        ).strip()
        if title and url:
            items.append((title, url, body))
    return items


class NewsSentimentAdapter(IMarketIntelProvider, IProviderDescribe):
    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._provider_config = config.providers.sentiment
        self._cache = resolve_cache_backend(config.cache)
        self._clients: dict[str, ResilientHttpClient] = {
            name: create_resilient_http_client(
                f"sentiment.{name}",
                source.rate_limits,
                cache_config=config.cache,
                http=source.http,
            )
            for name, source in self._source_configs().items()
        }

    async def describe(self) -> dict[str, Any]:
        sources = {
            name: settings_snapshot(source) for name, source in self._source_configs().items()
        }
        return provider_descriptor(
            provider_id="sentiment",
            capability="intel",
            provider_config=self._provider_config,
            details={"sources": sources},
        )

    def _source_configs(self) -> dict[str, SentimentSourceConfig]:
        sources = self._provider_config.sources
        return {
            "google_news": sources.google_news,
            "stocktwits": sources.stocktwits,
            "reddit": sources.reddit,
        }

    def _source_cache_key(self, source_name: str, suffix: str) -> str:
        return f"intel_src_{source_name}_{suffix}"

    def _client_for(self, source_name: str) -> ResilientHttpClient | None:
        source = self._source_configs().get(source_name)
        if source is None or not source.enabled:
            return None
        return self._clients[source_name]

    def _stocktwits_headers(self) -> dict[str, str]:
        headers = dict(_STOCKTWITS_HEADERS)
        user_agent = self._provider_config.sources.stocktwits.http.user_agent
        if user_agent:
            headers["User-Agent"] = user_agent
        return headers

    async def _fetch_stocktwits_stream(self, symbol: str) -> dict[str, Any] | None:
        client = self._client_for("stocktwits")
        if client is None:
            return None
        url = _STOCKTWITS_STREAM_URL.format(symbol=symbol)
        try:
            response = await client.request("GET", url, headers=self._stocktwits_headers())
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except FinpipeProviderDownError as exc:
            if any(code in str(exc) for code in ("403", "404")):
                logger.info("Stocktwits stream unavailable for %s: %s", symbol, exc)
                return None
            logger.warning("Stocktwits fetch failed for %s: %s", symbol, exc)
            return None
        except Exception as exc:
            logger.warning("Stocktwits fetch failed for %s: %s", symbol, exc)
            return None

    async def _fetch_reddit_entries(self, symbol: str) -> list[tuple[str, str, str]]:
        client = self._client_for("reddit")
        if client is None:
            return []

        entries: list[tuple[str, str, str]] = []
        for subreddit in _REDDIT_FORUM_SUBREDDITS:
            url = _reddit_search_url(symbol, subreddit)
            try:
                response = await client.request("GET", url)
                response.raise_for_status()
                entries.extend(
                    _parse_reddit_atom_feed(response.text)[:_REDDIT_ENTRIES_PER_SUBREDDIT]
                )
            except FinpipeProviderDownError as exc:
                logger.info("Reddit RSS skipped for %s/%s: %s", subreddit, symbol, exc)
            except Exception as exc:
                logger.warning("Reddit RSS failed for %s/%s: %s", subreddit, symbol, exc)
        return entries

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()

    async def _fetch_google_news(self, symbol: str | None, limit: int) -> list[NewsArticle]:
        client = self._client_for("google_news")
        if client is None:
            return []

        cache_key = self._source_cache_key("google_news", f"{symbol}_{limit}")
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [NewsArticle(**item) for item in cached]

        query = symbol if symbol else "market news"
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
        source_cfg = self._provider_config.sources.google_news
        headers = {}
        if source_cfg.http.user_agent:
            headers["User-Agent"] = source_cfg.http.user_agent

        try:
            response = await client.request("GET", url, headers=headers or None)
            root = ET.fromstring(response.text)
            articles: list[NewsArticle] = []
            for item in root.findall(".//item")[:limit]:
                title = item.findtext("title") or ""
                link = item.findtext("link") or ""
                pub_date_str = item.findtext("pubDate")
                try:
                    dt = parsedate_to_datetime(pub_date_str) if pub_date_str else datetime.now()
                except (TypeError, ValueError):
                    dt = datetime.now()
                articles.append(
                    NewsArticle(
                        title=title,
                        link=link,
                        published_at=dt,
                        publisher="Google News",
                        related_tickers=[symbol] if symbol else [],
                    )
                )
            self._cache.set(
                cache_key,
                [article.model_dump() for article in articles],
                self._provider_config.resolve_source_fetch_ttl("google_news"),
            )
            return articles
        except Exception as exc:
            logger.warning("Google News RSS failed: %s", exc)
            return []

    async def get_news(self, symbol: str | None = None, limit: int = 20) -> list[NewsArticle]:
        cache_key = f"news_{symbol}_{limit}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [NewsArticle(**item) for item in cached]

        fetchers = []
        if self._client_for("google_news") is not None:
            fetchers.append(self._fetch_google_news(symbol, limit))

        if not fetchers:
            return []

        results = await asyncio.gather(*fetchers)
        all_articles: list[NewsArticle] = []
        for batch in results:
            all_articles.extend(batch)
        all_articles.sort(key=lambda article: article.published_at, reverse=True)
        final_list = all_articles[:limit]
        self._cache.set(
            cache_key,
            [article.model_dump() for article in final_list],
            self._provider_config.ttls.news_sec,
        )
        return final_list

    async def _fetch_stocktwits_sentiment(self, symbol: str) -> tuple[int, int]:
        cache_key = self._source_cache_key("stocktwits", symbol)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return int(cached[0]), int(cached[1])

        data = await self._fetch_stocktwits_stream(symbol)
        if data is None:
            return 0, 0

        bullish = 0
        bearish = 0
        for msg in data.get("messages", []):
            label = _stocktwits_message_sentiment(msg)
            if label == "bullish":
                bullish += 1
            elif label == "bearish":
                bearish += 1
        counts = (bullish, bearish)
        self._cache.set(
            cache_key,
            list(counts),
            self._provider_config.resolve_source_fetch_ttl("stocktwits"),
        )
        return counts

    async def _fetch_reddit_sentiment(self, symbol: str) -> tuple[int, int]:
        cache_key = self._source_cache_key("reddit", symbol)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return int(cached[0]), int(cached[1])

        bullish = 0
        bearish = 0
        for title, _, _ in await self._fetch_reddit_entries(symbol):
            title_lower = title.lower()
            if any(word in title_lower for word in _REDDIT_BULL_KEYWORDS):
                bullish += 1
            if any(word in title_lower for word in _REDDIT_BEAR_KEYWORDS):
                bearish += 1
        counts = (bullish, bearish)
        self._cache.set(
            cache_key,
            list(counts),
            self._provider_config.resolve_source_fetch_ttl("reddit"),
        )
        return counts

    async def _fetch_stocktwits_posts(self, symbol: str, limit: int = 30) -> list[SocialPost]:
        cache_key = self._source_cache_key("stocktwits", f"msgs_{symbol}_{limit}")
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [SocialPost(**item) for item in cached]

        data = await self._fetch_stocktwits_stream(symbol)
        if data is None:
            return []

        posts: list[SocialPost] = []
        for msg in data.get("messages", [])[:limit]:
            body = msg.get("body", "")
            user = msg.get("user", {})
            username = user.get("username", "unknown")
            msg_id = msg.get("id", "")
            if not body or not msg_id:
                continue
            posts.append(
                SocialPost(
                    kind=SocialPostKind.MICROBLOG,
                    text=body,
                    url=f"https://stocktwits.com/{username}/message/{msg_id}",
                    author=username,
                    created_at=None,
                )
            )
        self._cache.set(
            cache_key,
            [post.model_dump() for post in posts],
            self._provider_config.resolve_source_fetch_ttl("stocktwits"),
        )
        return posts

    async def _fetch_reddit_posts(self, symbol: str, limit: int = 25) -> list[SocialPost]:
        cache_key = self._source_cache_key("reddit", f"posts_{symbol}_{limit}")
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [SocialPost(**item) for item in cached]

        entries = await self._fetch_reddit_entries(symbol)
        posts: list[SocialPost] = []
        for title, post_url, description in entries[:limit]:
            posts.append(
                SocialPost(
                    kind=SocialPostKind.FORUM,
                    text=description,
                    title=title,
                    url=post_url,
                )
            )
        self._cache.set(
            cache_key,
            [post.model_dump() for post in posts],
            self._provider_config.resolve_source_fetch_ttl("reddit"),
        )
        return posts

    async def get_social_posts(
        self,
        symbol: str,
        *,
        limit: int = 30,
        kind: SocialPostKind | None = None,
    ) -> list[SocialPost]:
        cache_key = f"social_{symbol}_{limit}_{kind or 'all'}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return [SocialPost(**item) for item in cached]

        fetch_tasks = []
        if kind in (None, SocialPostKind.MICROBLOG) and self._client_for("stocktwits"):
            fetch_tasks.append(self._fetch_stocktwits_posts(symbol, limit))
        if kind in (None, SocialPostKind.FORUM) and self._client_for("reddit"):
            fetch_tasks.append(self._fetch_reddit_posts(symbol, limit))

        if not fetch_tasks:
            return []

        batches = await asyncio.gather(*fetch_tasks)
        posts: list[SocialPost] = []
        for batch in batches:
            posts.extend(batch)
        posts = posts[:limit]
        self._cache.set(
            cache_key,
            [post.model_dump() for post in posts],
            self._provider_config.ttls.news_sec,
        )
        return posts

    async def get_sentiment_score(self, symbol: str) -> SentimentScore:
        cache_key = f"sentiment_{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return SentimentScore(**cached)

        tasks = []
        sources_used: list[str] = []
        if self._client_for("stocktwits") is not None:
            tasks.append(self._fetch_stocktwits_sentiment(symbol))
            sources_used.append("microblog")
        if self._client_for("reddit") is not None:
            tasks.append(self._fetch_reddit_sentiment(symbol))
            sources_used.append("forum")

        results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []
        total_bullish = 0
        total_bearish = 0
        for result in results:
            if isinstance(result, tuple) and len(result) == 2:
                total_bullish += result[0]
                total_bearish += result[1]

        total = total_bullish + total_bearish
        score = (total_bullish - total_bearish) / total if total > 0 else 0.0
        sentiment = SentimentScore(
            symbol=symbol,
            source="+".join(sources_used) if sources_used else "none",
            timestamp=datetime.now(),
            score=score,
            magnitude=float(total),
        )
        self._cache.set(
            cache_key, sentiment.model_dump(), self._provider_config.ttls.sentiment_score_sec
        )
        return sentiment


@register_provider("sentiment", category="intel")
def build_sentiment(ctx: BuildContext) -> NewsSentimentAdapter:
    return NewsSentimentAdapter(ctx.config)
