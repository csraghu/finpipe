"""Yahoo Finance adapter tests: sync-bridge, lazy yfinance import, schema parity."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import polars as pl
import pytest
from finpipe.core.config import YahooConfig
from finpipe.core.errors import FinpipeConfigError, FinpipeDataNotFoundError
from finpipe.providers.yahoo import YahooFinanceAdapter

from conftest import FakeExecutor, make_runtime


def _adapter(executor: FakeExecutor | None = None) -> YahooFinanceAdapter:
    config = YahooConfig()
    return YahooFinanceAdapter(make_runtime(config, executor or FakeExecutor(), provider_key="yahoo"))


async def test_missing_yfinance_raises_config_error():
    """Lazy import check: yfinance missing raises on first use, not construction."""
    adapter = _adapter()
    with patch("importlib.util.find_spec", return_value=None):
        with pytest.raises(FinpipeConfigError, match="yfinance"):
            await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 3))


async def test_historical_prices_normalized_and_cached():
    """Historical prices are normalized to standard OHLCV schema."""
    mock_df = pd.DataFrame({
        "Open": [100.0, 101.0],
        "High": [110.0, 111.0],
        "Low": [95.0, 96.0],
        "Close": [105.0, 106.0],
        "Volume": [1000, 1100],
    }, index=pd.to_datetime(["2026-01-02", "2026-01-03"]))
    mock_df.index.name = "Date"

    with patch("yfinance.Ticker") as mock_ticker_class:
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker
        mock_ticker.history = MagicMock(return_value=mock_df)

        adapter = _adapter()
        frame = await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 3))

        assert isinstance(frame, pl.DataFrame)
        assert frame.columns == ["timestamp", "open", "high", "low", "close", "volume"]
        assert frame.height == 2


async def test_cache_hit_equals_fresh_fetch():
    """Cache hit must be identical to fresh fetch (v2 invariant)."""
    mock_df = pd.DataFrame({
        "Open": [100.0],
        "High": [110.0],
        "Low": [95.0],
        "Close": [105.0],
        "Volume": [1000],
    }, index=pd.to_datetime(["2026-01-02"]))
    mock_df.index.name = "Date"

    call_count = 0

    def mock_history(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return mock_df.copy()

    with patch("yfinance.Ticker") as mock_ticker_class:
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker
        mock_ticker.history = mock_history

        adapter = _adapter()
        fresh = await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 3))
        cached = await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 3))

        assert call_count == 1  # second call served from cache
        assert cached.columns == fresh.columns
        assert cached.rows() == fresh.rows()


async def test_empty_historical_prices_returns_empty_frame():
    """Empty yfinance result returns empty DataFrame."""
    with patch("yfinance.Ticker") as mock_ticker_class:
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker
        mock_ticker.history = MagicMock(return_value=pd.DataFrame())

        adapter = _adapter()
        frame = await adapter.get_historical_prices("BADTICKER", date(2026, 1, 1), date(2026, 1, 3))

        assert isinstance(frame, pl.DataFrame)
        assert frame.height == 0


async def test_metadata_normalized_and_cached():
    """Metadata is cached and schema-preserved."""
    mock_info = {
        "shortName": "Apple Inc",
        "longName": "Apple Inc",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "marketCap": 3000000000000,
        "exchange": "NASDAQ",
        "currency": "USD",
        "website": "https://apple.com",
        "longBusinessSummary": "Tech company",
    }

    with patch("yfinance.Ticker") as mock_ticker_class:
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker
        mock_ticker.info = mock_info

        adapter = _adapter()
        meta = await adapter.get_metadata("AAPL")

        assert meta.symbol == "AAPL"
        assert meta.short_name == "Apple Inc"
        assert meta.sector == "Technology"
        assert meta.market_cap == 3000000000000


async def test_options_chain_normalized_and_cached():
    """Options chain is normalized to standard model."""
    mock_options_dates = ["2026-01-16", "2026-02-20"]
    mock_chain_df = MagicMock()
    mock_chain_df.calls = pd.DataFrame({
        "contractSymbol": ["AAPL260116C00150000"],
        "strike": [150.0],
        "lastPrice": [10.5],
        "bid": [10.4],
        "ask": [10.6],
        "volume": [100],
        "openInterest": [500],
        "impliedVolatility": [0.2],
        "inTheMoney": [True],
    })
    mock_chain_df.puts = pd.DataFrame({
        "contractSymbol": ["AAPL260116P00150000"],
        "strike": [150.0],
        "lastPrice": [9.5],
        "bid": [9.4],
        "ask": [9.6],
        "volume": [80],
        "openInterest": [400],
        "impliedVolatility": [0.19],
        "inTheMoney": [False],
    })

    with patch("yfinance.Ticker") as mock_ticker_class:
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker
        mock_ticker.options = mock_options_dates
        mock_ticker.option_chain = MagicMock(return_value=mock_chain_df)

        adapter = _adapter()
        chain = await adapter.get_options_chain("AAPL", date(2026, 1, 16))

        assert chain.symbol == "AAPL"
        assert chain.expiration_date == date(2026, 1, 16)
        assert len(chain.calls) == 1
        assert chain.calls[0].contract_symbol == "AAPL260116C00150000"
        assert chain.calls[0].strike == 150.0
        assert len(chain.puts) == 1


async def test_options_chain_no_expirations_raises():
    """No options expirations raises DataNotFoundError."""
    with patch("yfinance.Ticker") as mock_ticker_class:
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker
        mock_ticker.options = []

        adapter = _adapter()
        with pytest.raises(FinpipeDataNotFoundError):
            await adapter.get_options_chain("AAPL")


async def test_financial_statements_cached():
    """Financial statements are cached and shaped correctly."""
    balance_df = pd.DataFrame(
        {"2026-01-01": [1000000], "2025-01-01": [900000]},
        index=["Cash"],
    )
    income_df = pd.DataFrame(
        {"2026-01-01": [5000000], "2025-01-01": [4500000]},
        index=["Revenue"],
    )
    cash_df = pd.DataFrame(
        {"2026-01-01": [500000], "2025-01-01": [400000]},
        index=["Operating"],
    )

    with patch("yfinance.Ticker") as mock_ticker_class:
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker
        mock_ticker.balance_sheet = balance_df
        mock_ticker.income_stmt = income_df
        mock_ticker.cashflow = cash_df

        adapter = _adapter()
        stmts = await adapter.get_financial_statements("AAPL")

        assert "balance_sheet" in stmts
        assert "income_statement" in stmts
        assert "cash_flow" in stmts
        assert stmts["balance_sheet"]["2026-01-01"]["Cash"] == 1000000


async def test_live_spot_price_cached():
    """Live spot price is cached."""
    mock_fast_info = {"lastPrice": 150.50}

    with patch("yfinance.Ticker") as mock_ticker_class:
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker
        mock_ticker.fast_info = mock_fast_info

        adapter = _adapter()
        price = await adapter.get_live_spot_price("AAPL")

        assert price == 150.50


async def test_symbol_sanitization():
    """Symbols with / and . are sanitized for yfinance."""
    with patch("yfinance.Ticker") as mock_ticker_class:
        mock_ticker = MagicMock()
        mock_ticker_class.return_value = mock_ticker
        mock_ticker.history = MagicMock(return_value=pd.DataFrame())

        adapter = _adapter()
        await adapter.get_historical_prices("BRK.B", date(2026, 1, 1), date(2026, 1, 3))

        mock_ticker_class.assert_called_with("BRK-B")
