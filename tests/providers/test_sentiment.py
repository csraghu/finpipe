import httpx
import pytest
import respx
from finpipe.core.config import FinpipeConfig
from finpipe.core.models import SocialPostKind
from finpipe.providers.sentiment import NewsSentimentAdapter, _stocktwits_message_sentiment


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
                        "stocktwits": {"http": {"transport": "httpx"}},
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
                        "stocktwits": {
                            "http": {"transport": "httpx"},
                            "ttls": {"fetch_sec": 3600},
                        },
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
async def test_news_sentiment_adapter_social_posts():
    from finpipe.core.config import FinpipeConfig
    config = FinpipeConfig.from_dict({
        "providers": {
            "sentiment": {
                "sources": {
                    "reddit": {
                        "client_id": "test",
                        "client_secret": "test"
                    }
                }
            }
        }
    })
    adapter = NewsSentimentAdapter(config)
    json_mock = {
        "data": {
            "children": [
                {
                    "data": {
                        "title": "AAPL moon",
                        "permalink": "/r/wallstreetbets/comments/abc/aapl/",
                        "selftext": "calls"
                    }
                }
            ]
        }
    }

    with respx.mock:
        respx.post("https://www.reddit.com/api/v1/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "mocked"})
        )
        respx.get(url__startswith="https://oauth.reddit.com").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        posts = await adapter.get_social_posts("AAPL", limit=5, kind=SocialPostKind.FORUM)
        assert len(posts) >= 1
        assert posts[0].kind == SocialPostKind.FORUM
        assert posts[0].title == "AAPL moon"


def test_stocktwits_legacy_sentiment_class_path():
    assert _stocktwits_message_sentiment({"sentiment": {"class": "Bullish"}}) == "bullish"
    assert (
        _stocktwits_message_sentiment({"entities": {"sentiment": {"basic": "Bearish"}}})
        == "bearish"
    )


@pytest.mark.asyncio
async def test_stocktwits_sentiment_uses_legacy_class_field(config):
    adapter = NewsSentimentAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://api.stocktwits.com").mock(
            return_value=httpx.Response(
                200,
                json={"messages": [{"sentiment": {"class": "bullish"}}]},
            )
        )
        bullish, bearish = await adapter._fetch_stocktwits_sentiment("AAPL")
        assert bullish == 1
        assert bearish == 0
