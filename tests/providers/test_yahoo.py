from datetime import date

import pandas as pd
import polars as pl
import pytest
from finpipe.providers.yahoo import YahooFinanceAdapter


@pytest.mark.asyncio
async def test_yahoo_historical_prices(config, mocker):
    adapter = YahooFinanceAdapter(config)

    # Mock yfinance ticker.history
    mock_df = pd.DataFrame(
        {"Open": [100.0], "High": [105.0], "Low": [99.0], "Close": [102.0], "Volume": [1000]},
        index=pd.DatetimeIndex(["2023-01-01"], name="Date"),
    )

    mock_ticker = mocker.MagicMock()
    mock_ticker.history.return_value = mock_df
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)

    df = await adapter.get_historical_prices("AAPL", date(2023, 1, 1), date(2023, 1, 2))
    assert isinstance(df, pl.DataFrame)
    assert "timestamp" in df.columns
    assert "close" in df.columns


@pytest.mark.asyncio
async def test_yahoo_metadata(config, mocker):
    adapter = YahooFinanceAdapter(config)

    mock_ticker = mocker.MagicMock()
    mock_ticker.info = {"shortName": "Apple Inc.", "marketCap": 3000000000000}
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)

    meta = await adapter.get_metadata("AAPL")
    assert meta.symbol == "AAPL"
    assert meta.short_name == "Apple Inc."
    assert meta.market_cap == 3000000000000


@pytest.mark.asyncio
async def test_yahoo_spot_price(config, mocker):
    adapter = YahooFinanceAdapter(config)
    mock_ticker = mocker.MagicMock()
    mock_ticker.fast_info = {"lastPrice": 150.5}
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)

    price = await adapter.get_live_spot_price("AAPL")
    assert price == 150.5


@pytest.mark.asyncio
async def test_yahoo_financials(config, mocker):
    adapter = YahooFinanceAdapter(config)
    mock_ticker = mocker.MagicMock()

    bs = pd.DataFrame({"2023": [100]}, index=["Cash"])
    inc = pd.DataFrame({"2023": [50]}, index=["Revenue"])
    cf = pd.DataFrame({"2023": [10]}, index=["Free Cash Flow"])

    mock_ticker.balance_sheet = bs
    mock_ticker.income_stmt = inc
    mock_ticker.cashflow = cf
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)

    fins = await adapter.get_financial_statements("AAPL")
    assert "balance_sheet" in fins


@pytest.mark.asyncio
async def test_yahoo_options_chain(config, mocker):
    adapter = YahooFinanceAdapter(config)
    mock_ticker = mocker.MagicMock()

    mock_ticker.options = ("2023-01-15",)

    mock_calls = pd.DataFrame(
        {
            "contractSymbol": ["AAPL230115C00150000"],
            "strike": [150.0],
            "lastPrice": [5.0],
            "bid": [4.9],
            "ask": [5.1],
            "volume": [100],
            "openInterest": [1000],
            "impliedVolatility": [0.3],
            "inTheMoney": [False],
        }
    )

    mock_chain = mocker.MagicMock()
    mock_chain.calls = mock_calls
    mock_chain.puts = pd.DataFrame(columns=mock_calls.columns)

    mock_ticker.option_chain.return_value = mock_chain
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)

    chain = await adapter.get_options_chain("AAPL", date(2023, 1, 15))
    assert chain.symbol == "AAPL"
    assert len(chain.calls) == 1
    assert chain.calls[0].strike == 150.0


@pytest.mark.asyncio
async def test_yahoo_options_snapshot(config, mocker):
    adapter = YahooFinanceAdapter(config)

    # We can reuse the chain fetching logic via mocking get_options_chain directly
    mock_chain = mocker.AsyncMock()
    mock_chain.calls = []
    mock_chain.puts = []

    mocker.patch.object(adapter, "get_options_chain", return_value=mock_chain)

    df = await adapter.get_options_snapshot("AAPL")
    assert df is not None


@pytest.mark.asyncio
async def test_yahoo_financials_cached(config, mocker):
    adapter = YahooFinanceAdapter(config)
    adapter._cache.set("yf_fin_AAPL", {"balance_sheet": {}}, 100)
    fins = await adapter.get_financial_statements("AAPL")
    assert "balance_sheet" in fins


@pytest.mark.asyncio
async def test_yahoo_options_chain_empty(config, mocker):
    adapter = YahooFinanceAdapter(config)
    mock_ticker = mocker.MagicMock()
    mock_ticker.options = ()
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)

    chain = await adapter.get_options_chain("AAPL")
    assert len(chain.calls) == 0


@pytest.mark.asyncio
async def test_yahoo_options_snapshot_populated(config, mocker):
    adapter = YahooFinanceAdapter(config)

    mock_chain = mocker.AsyncMock()
    from finpipe.core.models import OptionContract

    mock_chain.calls = [OptionContract(contract_symbol="C1", strike=10.0, in_the_money=False)]
    mock_chain.puts = [OptionContract(contract_symbol="P1", strike=10.0, in_the_money=True)]

    mocker.patch.object(adapter, "get_options_chain", return_value=mock_chain)

    df = await adapter.get_options_snapshot("AAPL")
    assert df.height == 2
    assert "type" in df.columns
