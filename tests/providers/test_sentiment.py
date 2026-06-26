import httpx
import pytest
import respx
from finpipe.core.config import FinpipeConfig
from finpipe.core.models import SocialPostKind
from finpipe.providers.sentiment import NewsSentimentAdapter


@pytest.mark.asyncio
async def test_news_sentiment_adapter_news(config):
    adapter = NewsSentimentAdapter(config)

    xml_mock = """<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel>
        <item><title>Test News</title><link>http://test</link>
        <pubDate>Wed, 01 Jan 2023 00:00:00 GMT</pubDate></item>
    </channel></rss>"""

    with respx.mock:
        respx.get(url__startswith="https://news.google.com").mock(
            return_value=httpx.Response(200, text=xml_mock)
        )
        news = await adapter.get_news("AAPL", limit=1)
        assert len(news) == 1
        assert news[0].title == "Test News"


@pytest.mark.asyncio
async def test_news_sentiment_adapter_score(config):
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "sentiment": {
                    "sources": {
                        "reddit": {"enabled": False},
                    }
                }
            }
        }
    )
    adapter = NewsSentimentAdapter(config)

    json_mock = {
        "messages": [
            {"entities": {"sentiment": {"basic": "Bullish"}}},
            {"entities": {"sentiment": {"basic": "Bullish"}}},
            {"entities": {"sentiment": {"basic": "Bearish"}}},
        ]
    }

    with respx.mock:
        respx.get(url__startswith="https://api.stocktwits.com").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        score = await adapter.get_sentiment_score("AAPL")
        assert score.magnitude == 3
        assert score.score > 0  # 2 bullish, 1 bearish


@pytest.mark.asyncio
async def test_sentiment_source_ttl_caches_fetch(config):
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "sentiment": {
                    "sources": {
                        "reddit": {"enabled": False},
                        "stocktwits": {"ttls": {"fetch_sec": 3600}},
                    }
                }
            }
        }
    )
    adapter = NewsSentimentAdapter(config)
    json_mock = {
        "messages": [{"entities": {"sentiment": {"basic": "Bullish"}}}],
    }

    with respx.mock:
        route = respx.get(url__startswith="https://api.stocktwits.com").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        await adapter._fetch_stocktwits_sentiment("AAPL")
        await adapter._fetch_stocktwits_sentiment("AAPL")
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_news_sentiment_adapter_social_posts(config):
    adapter = NewsSentimentAdapter(config)
    json_mock = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": "AAPL moon",
                        "selftext": "calls",
                        "permalink": "/r/wsb/comments/abc/aapl/",
                    }
                }
            ]
        }
    }

    with respx.mock:
        respx.get(url__startswith="https://www.reddit.com").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        posts = await adapter.get_social_posts("AAPL", limit=5, kind=SocialPostKind.FORUM)
        assert len(posts) == 1
        assert posts[0].kind == SocialPostKind.FORUM
        assert posts[0].title == "AAPL moon"
