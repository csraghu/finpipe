from __future__ import annotations

import httpx
import pytest
import respx
from finpipe.core.config import FinpipeConfig
from finpipe.core.models import SocialPostKind
from finpipe.providers.sentiment import NewsSentimentAdapter


@pytest.mark.asyncio
async def test_sentiment_describe(config):
    adapter = NewsSentimentAdapter(config)
    info = await adapter.describe()
    assert info["provider_id"] == "sentiment"
    assert "google_news" in info["details"]["sources"]


@pytest.mark.asyncio
async def test_sentiment_google_news_cache_and_bad_date(config):
    adapter = NewsSentimentAdapter(config)
    xml_mock = """<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel>
        <item><title>Bad Date</title><link>http://test</link>
        <pubDate>not-a-date</pubDate></item>
    </channel></rss>"""
    with respx.mock:
        respx.get(url__startswith="https://news.google.com").mock(
            return_value=httpx.Response(200, text=xml_mock)
        )
        articles = await adapter.get_news("AAPL", limit=1)
        assert len(articles) == 1
        cached = await adapter.get_news("AAPL", limit=1)
        assert cached[0].title == "Bad Date"


@pytest.mark.asyncio
async def test_sentiment_google_news_failure_returns_empty(config):
    adapter = NewsSentimentAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://news.google.com").mock(
            side_effect=httpx.ConnectError("down")
        )
        assert await adapter._fetch_google_news("AAPL", 1) == []


@pytest.mark.asyncio
async def test_sentiment_reddit_sentiment_and_posts(config):
    adapter = NewsSentimentAdapter(config)
    payload = {
        "data": {
            "children": [
                {"data": {"title": "AAPL calls moon", "selftext": "", "permalink": "/r/wsb/1"}},
                {"data": {"title": "puts tank", "selftext": "", "permalink": "/r/wsb/2"}},
            ]
        }
    }
    with respx.mock:
        respx.get(url__startswith="https://www.reddit.com").mock(
            return_value=httpx.Response(200, json=payload)
        )
        bullish, bearish = await adapter._fetch_reddit_sentiment("AAPL")
        assert bullish >= 1
        assert bearish >= 1
        posts = await adapter.get_social_posts("AAPL", limit=5, kind=SocialPostKind.FORUM)
        assert posts


@pytest.mark.asyncio
async def test_sentiment_stocktwits_posts_and_failures(config):
    adapter = NewsSentimentAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://api.stocktwits.com").mock(
            return_value=httpx.Response(
                200,
                json={
                    "messages": [
                        {
                            "id": "1",
                            "body": "bullish",
                            "user": {"username": "trader"},
                        }
                    ]
                },
            )
        )
        posts = await adapter.get_social_posts("AAPL", limit=5, kind=SocialPostKind.MICROBLOG)
        assert posts[0].kind == SocialPostKind.MICROBLOG

    with respx.mock:
        respx.get(url__startswith="https://api.stocktwits.com").mock(
            side_effect=httpx.ConnectError("down")
        )
        assert await adapter._fetch_stocktwits_posts("AAPL") == []


@pytest.mark.asyncio
async def test_sentiment_get_sentiment_score_aggregates(config):
    adapter = NewsSentimentAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://api.stocktwits.com").mock(
            return_value=httpx.Response(
                200,
                json={"messages": [{"entities": {"sentiment": {"basic": "Bullish"}}}]},
            )
        )
        respx.get(url__startswith="https://www.reddit.com").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"children": [{"data": {"title": "calls moon", "permalink": "/x"}}]}},
            )
        )
        score = await adapter.get_sentiment_score("AAPL")
        assert score.magnitude > 0


@pytest.mark.asyncio
async def test_sentiment_disabled_sources_return_empty(config):
    cfg = FinpipeConfig.from_dict(
        {
            "providers": {
                "sentiment": {
                    "sources": {
                        "google_news": {"enabled": False},
                        "stocktwits": {"enabled": False},
                        "reddit": {"enabled": False},
                    }
                }
            }
        }
    )
    adapter = NewsSentimentAdapter(cfg)
    assert await adapter.get_news("AAPL") == []
    assert await adapter.get_social_posts("AAPL") == []
    assert (await adapter.get_sentiment_score("AAPL")).magnitude == 0


@pytest.mark.asyncio
async def test_sentiment_close(config):
    adapter = NewsSentimentAdapter(config)
    await adapter.close()
