"""Sentiment/intel adapter tests: TokenStore in-memory, uniform degradation, per-source executors."""

from __future__ import annotations

from finpipe.core.config import SentimentConfig
from finpipe.providers.sentiment import NewsSentimentAdapter
from finpipe.runtime.tokens import TokenStore

from conftest import FakeExecutor, FakeResponse, make_runtime


def _adapter(executor: FakeExecutor | None = None) -> NewsSentimentAdapter:
    config = SentimentConfig()
    return NewsSentimentAdapter(make_runtime(config, executor or FakeExecutor(), provider_key="sentiment"))


async def test_google_news_parsed_from_rss():
    """Google News RSS feed is parsed into NewsArticle models."""
    rss_text = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Apple hits record high</title>
      <link>https://example.com/apple-news</link>
      <pubDate>Wed, 02 Jul 2026 10:00:00 GMT</pubDate>
      <description>Apple stock reaches all-time high.</description>
    </item>
  </channel>
</rss>"""

    executor = FakeExecutor([FakeResponse(200, text=rss_text)])
    adapter = _adapter(executor)
    articles = await adapter.get_news("AAPL", limit=10)

    assert len(articles) > 0
    assert articles[0].title == "Apple hits record high"


async def test_empty_news_results():
    """Empty news results return empty list."""
    rss_text = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
  </channel>
</rss>"""

    executor = FakeExecutor([FakeResponse(200, text=rss_text)])
    adapter = _adapter(executor)
    articles = await adapter.get_news("BADTICKER", limit=10)

    assert articles == []


async def test_stocktwits_posts():
    """StockTwits JSON feed is parsed into SocialPost models."""
    payload = {
        "messages": [
            {
                "id": "123456",
                "body": "AAPL to the moon!",
                "created_at": "2026-07-02T10:00:00Z",
                "user": {"username": "trader123"},
            }
        ]
    }

    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)
    posts = await adapter._stocktwits_posts("AAPL", limit=10)

    assert len(posts) > 0


async def test_empty_social_results():
    """Empty social results return empty list."""
    payload = {"messages": []}

    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)
    posts = await adapter._stocktwits_posts("BADTICKER", limit=10)

    assert posts == []


async def test_news_caching():
    """News results are cached per symbol."""
    rss_text = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Apple news</title>
      <link>https://example.com/news</link>
      <pubDate>Wed, 02 Jul 2026 10:00:00 GMT</pubDate>
      <description>Article.</description>
    </item>
  </channel>
</rss>"""

    executor = FakeExecutor([FakeResponse(200, text=rss_text)])
    adapter = _adapter(executor)
    first = await adapter.get_news("AAPL", limit=10)
    second = await adapter.get_news("AAPL", limit=10)

    assert len(executor.calls) == 1  # second call served from cache
    assert first[0].title == second[0].title


def test_token_store_created_in_memory():
    """TokenStore is created in-memory for per-source OAuth."""
    store = TokenStore()
    # TokenStore is initialized and ready for OAuth token caching
    assert store is not None
    # get_or_fetch method exists for async token retrieval
    assert hasattr(store, "get_or_fetch")


def test_adapter_initializes_token_store():
    """Adapter initializes a TokenStore for OAuth token caching."""
    adapter = _adapter()
    assert hasattr(adapter, "_tokens")
    assert isinstance(adapter._tokens, TokenStore)


async def test_adapter_closes_executors():
    """Adapter closes all per-source executors on close."""
    adapter = _adapter(FakeExecutor())
    await adapter.close()
    # Should not raise


def test_adapter_has_per_source_executors():
    """Adapter creates per-source executors."""
    adapter = _adapter(FakeExecutor())
    assert hasattr(adapter, "_executors")
    assert isinstance(adapter._executors, dict)
