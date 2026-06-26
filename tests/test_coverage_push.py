from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import httpx
import pandas as pd
import pytest
import respx
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.health.service import HealthService
from finpipe.providers.alpha_vantage import AlphaVantageAdapter
from finpipe.providers.fred import FredAdapter
from finpipe.providers.massive import MassiveOptionsAdapter
from finpipe.providers.yahoo import YahooFinanceAdapter


@pytest.mark.asyncio
async def test_yahoo_historical_cache_hit_and_pandas_format(pandas_config, mocker):
    adapter = YahooFinanceAdapter(pandas_config)
    adapter._cache.set(
        "yf_hist_AAPL_2026-01-01_2026-01-02_1d",
        {"timestamp": ["2026-01-01"], "close": [1.0]},
        60,
    )
    df = await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 2))
    assert isinstance(df, pd.DataFrame)


@pytest.mark.asyncio
async def test_yahoo_spot_and_metadata_cache_hits(config, mocker):
    adapter = YahooFinanceAdapter(config)
    adapter._cache.set("yf_spot_AAPL", 123.0, 60)
    assert await adapter.get_live_spot_price("AAPL") == 123.0

    adapter._cache.set(
        "yf_meta_AAPL",
        {"symbol": "AAPL", "short_name": "Apple"},
        60,
    )
    meta = await adapter.get_metadata("AAPL")
    assert meta.symbol == "AAPL"


@pytest.mark.asyncio
async def test_yahoo_circuit_breaker_raises(config, mocker):
    adapter = YahooFinanceAdapter(config)

    async def _broken(*_args, **_kwargs):
        raise FinpipeProviderDownError("Yahoo Finance Circuit breaker tripped")

    mocker.patch.object(adapter, "_execute_with_resilience", side_effect=_broken)
    with pytest.raises(FinpipeProviderDownError, match="Circuit breaker"):
        await adapter.get_live_spot_price("AAPL")


@pytest.mark.asyncio
async def test_yahoo_empty_history_formats_columns(pandas_config, mocker):
    adapter = YahooFinanceAdapter(pandas_config)
    mock_ticker = mocker.MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)
    df = await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 2))
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


@pytest.mark.asyncio
async def test_fred_cache_hit_and_empty_series(pandas_config):
    adapter = FredAdapter(pandas_config)
    adapter._cache.set(
        "fred_DGS10_2026-01-01_2026-01-31",
        {"timestamp": [], "value": []},
        60,
    )
    df = await adapter.get_macro_series("DGS10", date(2026, 1, 1), date(2026, 1, 31))
    assert isinstance(df, pd.DataFrame)


@pytest.mark.asyncio
async def test_fred_fetch_with_observations(config):
    adapter = FredAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://api.stlouisfed.org/fred/series/observations").mock(
            return_value=httpx.Response(
                200,
                json={"observations": [{"date": "2026-01-01", "value": "1.5"}]},
            )
        )
        df = await adapter.get_macro_series("DGS10", date(2026, 1, 1), date(2026, 1, 31))
        assert not df.is_empty()


@pytest.mark.asyncio
async def test_alpha_historical_cache_hit(pandas_config):
    adapter = AlphaVantageAdapter(pandas_config)
    adapter._cache.set(
        "av_hist_AAPL_1d",
        {"timestamp": ["2026-01-01"], "close": [1.0]},
        60,
    )
    df = await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 2))
    assert isinstance(df, pd.DataFrame)


@pytest.mark.asyncio
async def test_sentiment_google_news_without_symbol(config):
    from finpipe.providers.sentiment import NewsSentimentAdapter

    adapter = NewsSentimentAdapter(config)
    xml_mock = """<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel>
        <item><title>Market</title><link>http://test</link>
        <pubDate>Wed, 01 Jan 2026 00:00:00 GMT</pubDate></item>
    </channel></rss>"""
    with respx.mock:
        respx.get(url__startswith="https://news.google.com").mock(
            return_value=httpx.Response(200, text=xml_mock)
        )
        articles = await adapter._fetch_google_news(None, 1)
        assert articles[0].title == "Market"
        assert articles[0].related_tickers == []


@pytest.mark.asyncio
async def test_screener_trending_cache_hit(config):
    from finpipe.providers.screener import ScreenerAdapter

    adapter = ScreenerAdapter(config)
    adapter._cache.set("screener_src_yahoo_trending_trending", ["MSFT"], 60)
    assert await adapter.get_trending() == ["MSFT"]
    await adapter.close()


@pytest.mark.asyncio
async def test_massive_describe_and_fetch_single_non_dict_result(config):
    adapter = MassiveOptionsAdapter(config)
    info = await adapter.describe()
    assert info["provider_id"] == "massive"
    with respx.mock:
        respx.get(url__startswith="https://api.massive.com/v3/snapshot/options/AAPL/").mock(
            return_value=httpx.Response(200, json={"results": "not-a-dict"})
        )
        assert await adapter.fetch_single_option_snapshot("AAPL", "O:TEST") == {}
    adapter = MassiveOptionsAdapter(config)
    await adapter.close()


@pytest.mark.asyncio
async def test_health_service_templates_and_generic_error(config):
    from finpipe.client import Client

    client = Client(config)
    service = HealthService(client)
    assert service.describe_probes()
    assert service.health_config_template()

    broken = MagicMock()
    broken.get_metadata = AsyncMock(side_effect=ValueError("boom"))
    equity = MagicMock()
    equity.provider = MagicMock(return_value=broken)
    client.catalog.capability = MagicMock(return_value=equity)
    cfg = FinpipeConfig.from_dict(
        {"health": {"enabled": True, "probes": {"equity.yahoo": {"enabled": True}}}}
    )
    client.config = cfg
    result = await service.check("equity.yahoo")
    assert result.status == "error"
