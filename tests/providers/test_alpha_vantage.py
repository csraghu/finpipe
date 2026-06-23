from datetime import date

import httpx
import pytest
import respx
from finpipe.core.exceptions import FinpipeDataNotFoundError
from finpipe.providers.alpha_vantage import AlphaVantageAdapter


@pytest.mark.asyncio
async def test_alpha_vantage_historical(config):
    adapter = AlphaVantageAdapter(config)

    csv_mock = "timestamp,open,high,low,close,volume\n2023-01-01,100,105,99,102,1000"

    with respx.mock:
        respx.get(url__startswith="https://www.alphavantage.co/query").mock(
            return_value=httpx.Response(200, text=csv_mock)
        )
        df = await adapter.get_historical_prices("AAPL", date(2023, 1, 1), date(2023, 1, 2))
        assert df.height == 1
        assert df.select("close").item() == 102.0


@pytest.mark.asyncio
async def test_alpha_vantage_metadata(config):
    adapter = AlphaVantageAdapter(config)

    json_mock = {"Symbol": "AAPL", "Name": "Apple Inc.", "MarketCapitalization": "3000000000"}

    with respx.mock:
        respx.get(url__startswith="https://www.alphavantage.co/query").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        meta = await adapter.get_metadata("AAPL")
        assert meta.symbol == "AAPL"
        assert meta.short_name == "Apple Inc."
        assert meta.market_cap == 3000000000.0


@pytest.mark.asyncio
async def test_alpha_vantage_historical_intraday(config):
    adapter = AlphaVantageAdapter(config)
    csv_mock = "timestamp,open,high,low,close,volume\n2023-01-01 09:30:00,100,101,99,100,1000"

    with respx.mock:
        respx.get("https://www.alphavantage.co/query").mock(
            return_value=httpx.Response(200, text=csv_mock)
        )
        df = await adapter.get_historical_prices(
            "AAPL", date(2023, 1, 1), date(2023, 1, 2), interval="5m"
        )
        assert df is not None


@pytest.mark.asyncio
async def test_alpha_vantage_historical_error(config):
    adapter = AlphaVantageAdapter(config)

    with respx.mock:
        respx.get("https://www.alphavantage.co/query").mock(
            return_value=httpx.Response(200, text='{"Error Message": "Invalid API call"}')
        )
        with pytest.raises(FinpipeDataNotFoundError):
            await adapter.get_historical_prices("AAPL", date(2023, 1, 1), date(2023, 1, 2))


@pytest.mark.asyncio
async def test_alpha_vantage_spot_price(config):
    adapter = AlphaVantageAdapter(config)

    json_mock = {"Global Quote": {"05. price": "150.50"}}

    with respx.mock:
        respx.get("https://www.alphavantage.co/query").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        price = await adapter.get_live_spot_price("AAPL")
        assert price == 150.50

        # Test cache
        price_cached = await adapter.get_live_spot_price("AAPL")
        assert price_cached == 150.50


@pytest.mark.asyncio
async def test_alpha_vantage_metadata_cache(config):
    adapter = AlphaVantageAdapter(config)
    json_mock = {"Symbol": "AAPL", "Name": "Apple Inc."}

    with respx.mock:
        respx.get("https://www.alphavantage.co/query").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        await adapter.get_metadata("AAPL")
        # Second call should hit cache
        await adapter.get_metadata("AAPL")


@pytest.mark.asyncio
async def test_alpha_vantage_financials(config):
    adapter = AlphaVantageAdapter(config)
    with pytest.raises(NotImplementedError):
        await adapter.get_financial_statements("AAPL")
