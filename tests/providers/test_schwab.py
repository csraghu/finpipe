from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.providers.schwab import SchwabAdapter


@pytest.fixture
def mock_schwab_config(monkeypatch):
    monkeypatch.setenv("SCHWAB_APP_KEY", "test_key")
    monkeypatch.setenv("SCHWAB_APP_SECRET", "test_secret")
    monkeypatch.setenv("SCHWAB_REFRESH_TOKEN", "test_refresh")
    return FinpipeConfig()

@pytest.fixture
def schwab_adapter(mock_schwab_config):
    adapter = SchwabAdapter(mock_schwab_config)
    adapter._client = AsyncMock()
    # Mock the get_access_token to skip OAuth calls
    adapter._get_access_token = AsyncMock(return_value="mock_access_token")
    return adapter

@pytest.mark.asyncio
async def test_get_historical_prices(schwab_adapter):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "candles": [
            {"datetime": 1609459200000, "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000}
        ]
    }
    schwab_adapter._client.get.return_value = mock_resp

    df = await schwab_adapter.get_historical_prices("AAPL", date(2021, 1, 1), date(2021, 1, 2))
    assert len(df) == 1
    assert "timestamp" in df.columns

@pytest.mark.asyncio
async def test_get_live_spot_price(schwab_adapter):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "AAPL": {"quote": {"lastPrice": 150.5}}
    }
    schwab_adapter._client.get.return_value = mock_resp

    price = await schwab_adapter.get_live_spot_price("AAPL")
    assert price == 150.5

@pytest.mark.asyncio
async def test_get_options_chain(schwab_adapter):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "SUCCESS",
        "callExpDateMap": {
            "2021-01-15:8": {
                "150.0": [{"symbol": "AAPL_011521C150", "strikePrice": 150.0, "last": 5.0, "bid": 4.9, "ask": 5.1}]
            }
        },
        "putExpDateMap": {}
    }
    schwab_adapter._client.get.return_value = mock_resp

    chain = await schwab_adapter.get_options_chain("AAPL", date(2021, 1, 15))
    assert chain.symbol == "AAPL"
    assert len(chain.calls) == 1
    assert chain.calls[0].strike == 150.0
    assert len(chain.puts) == 0

@pytest.mark.asyncio
async def test_metadata(schwab_adapter):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "AAPL": {"reference": {"description": "Apple Inc.", "exchangeName": "NASDAQ"}}
    }
    schwab_adapter._client.get.return_value = mock_resp

    meta = await schwab_adapter.get_metadata("AAPL")
    assert meta.short_name == "Apple Inc."
    assert meta.exchange == "NASDAQ"
