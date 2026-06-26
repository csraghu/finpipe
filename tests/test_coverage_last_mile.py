from datetime import date
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from finpipe.core.config import FinpipeConfig
from finpipe.core.models import OptionChain, OptionContract
from finpipe.providers.screener import ScreenerAdapter
from finpipe.providers.sentiment import NewsSentimentAdapter
from finpipe.providers.yahoo import YahooFinanceAdapter


@pytest.mark.asyncio
async def test_sentiment_google_news_cache_and_user_agent(config):
    cfg = FinpipeConfig.from_dict(
        {
            "providers": {
                "sentiment": {
                    "sources": {
                        "google_news": {
                            "enabled": True,
                            "http": {"user_agent": "custom-agent"},
                        }
                    }
                }
            }
        }
    )
    adapter = NewsSentimentAdapter(cfg)
    adapter._cache.set(
        "intel_src_google_news_AAPL_1",
        [
            {
                "title": "Cached",
                "link": "l",
                "published_at": "2026-01-01T00:00:00",
                "publisher": "Google News",
                "related_tickers": ["AAPL"],
            }
        ],
        60,
    )
    articles = await adapter._fetch_google_news("AAPL", 1)
    assert articles[0].title == "Cached"


@pytest.mark.asyncio
async def test_sentiment_stocktwits_disabled_returns_zero(config):
    cfg = FinpipeConfig.from_dict(
        {"providers": {"sentiment": {"sources": {"stocktwits": {"enabled": False}}}}}
    )
    adapter = NewsSentimentAdapter(cfg)
    assert await adapter._fetch_stocktwits_sentiment("AAPL") == (0, 0)
    assert await adapter._fetch_stocktwits_posts("AAPL") == []


@pytest.mark.asyncio
async def test_sentiment_reddit_posts_cache_hit(config):
    adapter = NewsSentimentAdapter(config)
    adapter._cache.set(
        "intel_src_reddit_posts_AAPL_25",
        [
            {
                "kind": "forum",
                "text": "body",
                "title": "title",
                "url": "https://reddit.com/x",
            }
        ],
        60,
    )
    posts = await adapter._fetch_reddit_posts("AAPL")
    assert posts[0].title == "title"


@pytest.mark.asyncio
async def test_screener_predefined_failure_and_headers(config):
    adapter = ScreenerAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://query1.finance.yahoo.com/v1/finance/screener").mock(
            side_effect=httpx.ConnectError("down")
        )
        assert await adapter.get_predefined("day_gainers") == []
    await adapter.close()


@pytest.mark.asyncio
async def test_massive_get_json_without_api_key(config):
    from finpipe.providers.massive import MassiveOptionsAdapter

    adapter = MassiveOptionsAdapter(config)
    adapter._api_key = None
    assert await adapter._get_json("https://api.massive.com/v1/options/chain") == {}


@pytest.mark.asyncio
async def test_screener_tradingview_disabled_returns_empty():
    from finpipe.providers.screener import ScreenerAdapter

    cfg = FinpipeConfig.from_dict(
        {
            "providers": {
                "screener": {"sources": {"tradingview": {"enabled": False}}},
            }
        }
    )
    adapter = ScreenerAdapter(cfg)
    assert await adapter.run_tradingview({"limit": 1}) == []
    await adapter.close()


@pytest.mark.asyncio
async def test_sentiment_google_disabled_returns_empty():
    cfg = FinpipeConfig.from_dict(
        {"providers": {"sentiment": {"sources": {"google_news": {"enabled": False}}}}}
    )
    adapter = NewsSentimentAdapter(cfg)
    assert await adapter._fetch_google_news("AAPL", 1) == []


@pytest.mark.asyncio
async def test_yahoo_fetch_snapshot_puts_and_limits(config, mocker):
    adapter = YahooFinanceAdapter(config)
    chain = OptionChain(
        symbol="AAPL",
        expiration_date=date(2026, 1, 15),
        calls=[],
        puts=[
            OptionContract(
                contract_symbol="P1",
                strike=50.0,
                last_price=1.0,
                in_the_money=True,
            ),
            OptionContract(
                contract_symbol="P2",
                strike=150.0,
                last_price=2.0,
                in_the_money=False,
            ),
        ],
    )
    mocker.patch.object(adapter, "get_options_chain", AsyncMock(return_value=chain))
    rows = await adapter.fetch_options_snapshot(
        "AAPL",
        expiration_date="2026-01-15",
        contract_type="put",
        strike_price_lte=100.0,
        limit=1,
    )
    assert len(rows) == 1
    assert rows[0]["details"]["ticker"] == "P1"


@pytest.mark.asyncio
async def test_yahoo_circuit_breaker_from_execute(config, mocker):
    import pybreaker
    from finpipe.core.exceptions import FinpipeProviderDownError

    adapter = YahooFinanceAdapter(config)

    async def inner():
        raise pybreaker.CircuitBreakerError("tripped")

    mocker.patch.object(adapter, "_breaker", side_effect=lambda fn: inner)
    adapter._limiter.acquire = mocker.AsyncMock()
    adapter._limiter.concurrency.limit = mocker.MagicMock(
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=None),
            __aexit__=mocker.AsyncMock(return_value=None),
        )
    )
    adapter._limiter.record_success = mocker.MagicMock()
    with pytest.raises(FinpipeProviderDownError):
        await adapter._execute_with_resilience(lambda: 1)
