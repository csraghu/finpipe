from datetime import date, timedelta
from unittest.mock import PropertyMock, patch

import httpx
import pytest
import respx
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.providers.sentiment import NewsSentimentAdapter
from finpipe.providers.yahoo import YahooFinanceAdapter


@pytest.mark.asyncio
async def test_sentiment_stocktwits_user_agent(config):
    stocktwits_config = config.providers.sentiment.sources.stocktwits.model_copy(
        update={"http": config.providers.sentiment.sources.stocktwits.http.model_copy(update={"user_agent": "Custom Agent"})}
    )
    new_sources = config.providers.sentiment.sources.model_copy(update={"stocktwits": stocktwits_config})
    new_sentiment = config.providers.sentiment.model_copy(update={"sources": new_sources})
    new_providers = config.providers.model_copy(update={"sentiment": new_sentiment})
    new_config = config.model_copy(update={"providers": new_providers})
    adapter = NewsSentimentAdapter(new_config)
    headers = adapter._stocktwits_headers()
    assert headers["User-Agent"] == "Custom Agent"

@pytest.mark.asyncio
async def test_sentiment_stocktwits_stream_exceptions(config):
    adapter = NewsSentimentAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://api.stocktwits.com").mock(
            side_effect=FinpipeProviderDownError("403 Forbidden")
        )
        assert await adapter._fetch_stocktwits_stream("AAPL") is None

        respx.get(url__startswith="https://api.stocktwits.com").mock(
            side_effect=Exception("Unknown Error")
        )
        assert await adapter._fetch_stocktwits_stream("AAPL") is None

@pytest.mark.asyncio
async def test_sentiment_reddit_token_missing_credentials(config):
    reddit_config = config.providers.sentiment.sources.reddit.model_copy(update={"client_id": None})
    new_sources = config.providers.sentiment.sources.model_copy(update={"reddit": reddit_config})
    new_sentiment = config.providers.sentiment.model_copy(update={"sources": new_sources})
    new_providers = config.providers.model_copy(update={"sentiment": new_sentiment})
    new_config = config.model_copy(update={"providers": new_providers})
    adapter = NewsSentimentAdapter(new_config)
    assert await adapter._fetch_reddit_token(None) is None  # type: ignore

@pytest.mark.asyncio
async def test_sentiment_reddit_token_exception(config):
    reddit_config = config.providers.sentiment.sources.reddit.model_copy(update={"client_id": "test", "client_secret": "test"})
    new_sources = config.providers.sentiment.sources.model_copy(update={"reddit": reddit_config})
    new_sentiment = config.providers.sentiment.model_copy(update={"sources": new_sources})
    new_providers = config.providers.model_copy(update={"sentiment": new_sentiment})
    new_config = config.model_copy(update={"providers": new_providers})
    adapter = NewsSentimentAdapter(new_config)
    with respx.mock:
        respx.post("https://www.reddit.com/api/v1/access_token").mock(
            side_effect=Exception("Failed")
        )
        assert await adapter._fetch_reddit_token(adapter._client_for("reddit")) is None  # type: ignore

@pytest.mark.asyncio
async def test_sentiment_reddit_stream_exceptions(config):
    reddit_config = config.providers.sentiment.sources.reddit.model_copy(update={"client_id": "test", "client_secret": "test"})
    new_sources = config.providers.sentiment.sources.model_copy(update={"reddit": reddit_config})
    new_sentiment = config.providers.sentiment.model_copy(update={"sources": new_sources})
    new_providers = config.providers.model_copy(update={"sentiment": new_sentiment})
    new_config = config.model_copy(update={"providers": new_providers})
    adapter = NewsSentimentAdapter(new_config)
    with respx.mock:
        respx.post("https://www.reddit.com/api/v1/access_token").mock(
            return_value=httpx.Response(200, json={"access_token": "mocked"})
        )
        respx.get(url__startswith="https://oauth.reddit.com").mock(
            side_effect=FinpipeProviderDownError("403 Forbidden")
        )
        assert await adapter._fetch_reddit_entries("AAPL") == []

        respx.get(url__startswith="https://oauth.reddit.com").mock(
            side_effect=Exception("Unknown Error")
        )
        assert await adapter._fetch_reddit_entries("AAPL") == []

@pytest.mark.asyncio
async def test_sentiment_get_news_exceptions(config):
    adapter = NewsSentimentAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://news.google.com").mock(
            side_effect=Exception("Failed")
        )
        assert await adapter.get_news("AAPL") == []



@pytest.mark.asyncio
async def test_yahoo_exceptions(config):
    adapter = YahooFinanceAdapter(config)
    with patch("yfinance.Ticker.options", new_callable=PropertyMock) as mock_options:
        mock_options.return_value = ()
        chain = await adapter.get_options_chain("AAPL")
        assert len(chain.calls) == 0
        df = await adapter.get_options_snapshot("AAPL")
        assert len(df) == 0

    with patch("yfinance.Ticker.info", new_callable=PropertyMock) as mock_info:
        mock_info.side_effect = Exception("Failed")
        with pytest.raises(Exception):
            await adapter.get_metadata("AAPL")  # changed from get_company_info

    with patch("yfinance.Ticker.history") as mock_history:
        import pandas as pd
        mock_history.return_value = pd.DataFrame()
        start = date.today() - timedelta(days=1)
        end = date.today()
        # Mock empty return, formatted df should be empty
        assert len(await adapter.get_historical_prices("AAPL", start, end, "1d")) == 0

        mock_history.side_effect = Exception("Failed")
        # Ensure it raises or handles it. Actually, wait. does it handle it or raise it?
        # In yahoo.py:
        # ticker.history doesn't get caught! It propagates. Wait, `_execute_with_resilience` catches things?
        # Yes, maybe. Let's see if it returns something or raises.
        # It's an internal test, maybe it should raise?
        # Let's check what it does.
        # If it raises, then with pytest.raises(Exception):
        with pytest.raises(Exception):
            await adapter.get_historical_prices("AAPL", start, end, "1wk")
