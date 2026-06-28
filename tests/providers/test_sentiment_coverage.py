import httpx
import pytest
import respx
from finpipe.providers.sentiment import NewsSentimentAdapter
from finpipe.providers.yahoo import YahooFinanceAdapter


@pytest.mark.asyncio
async def test_sentiment_stocktwits_and_reddit_failure_paths(config):
    adapter = NewsSentimentAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://api.stocktwits.com").mock(
            side_effect=httpx.ConnectError("down")
        )
        assert await adapter._fetch_stocktwits_sentiment("AAPL") == (0, 0)
        assert await adapter._fetch_stocktwits_posts("AAPL") == []

        respx.get(url__startswith="https://www.reddit.com").mock(
            side_effect=httpx.ConnectError("down")
        )
        assert await adapter._fetch_reddit_sentiment("AAPL") == (0, 0)
        assert await adapter._fetch_reddit_posts("AAPL") == []


@pytest.mark.asyncio
async def test_sentiment_stocktwits_skips_empty_messages(config):
    adapter = NewsSentimentAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://api.stocktwits.com").mock(
            return_value=httpx.Response(
                200,
                json={"messages": [{"id": "", "body": "", "user": {"username": "x"}}]},
            )
        )
        assert await adapter._fetch_stocktwits_posts("AAPL") == []


@pytest.mark.asyncio
async def test_sentiment_reddit_skips_empty_posts(config):
    adapter = NewsSentimentAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://www.reddit.com").mock(
            return_value=httpx.Response(
                200,
                text=(
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<feed xmlns="http://www.w3.org/2005/Atom">'
                    '<entry><title></title><link href="" /></entry>'
                    "</feed>"
                ),
            )
        )
        assert await adapter._fetch_reddit_posts("AAPL") == []


@pytest.mark.asyncio
async def test_sentiment_source_cache_hits(config):
    adapter = NewsSentimentAdapter(config)
    adapter._cache.set("intel_src_stocktwits_AAPL", [2, 1], 60)
    assert await adapter._fetch_stocktwits_sentiment("AAPL") == (2, 1)
    adapter._cache.set("intel_src_reddit_AAPL", [1, 0], 60)
    assert await adapter._fetch_reddit_sentiment("AAPL") == (1, 0)


@pytest.mark.asyncio
async def test_yahoo_options_chain_empty_execute_result(config, mocker):
    adapter = YahooFinanceAdapter(config)
    mock_ticker = mocker.MagicMock()
    mock_ticker.options = ("2026-01-15",)
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)
    mocker.patch.object(adapter, "_execute_with_resilience", return_value=None)
    chain = await adapter.get_options_chain("AAPL")
    assert chain.symbol == "AAPL"
