"""Alpha Vantage adapter tests: outputsize correctness + soft-429 AIMD feedback."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest
from finpipe.core.config import AlphaVantageConfig
from finpipe.core.errors import FinpipeDataNotFoundError, FinpipeRateLimitExceededError
from finpipe.providers.alpha_vantage import AlphaVantageAdapter
from pydantic import SecretStr

from conftest import FakeExecutor, FakeResponse, make_runtime


def _adapter(executor: FakeExecutor) -> AlphaVantageAdapter:
    config = AlphaVantageConfig(api_key=SecretStr("avk"))
    return AlphaVantageAdapter(make_runtime(config, executor, provider_key="alpha_vantage"))


async def test_historical_normalized_and_range_sliced():
    end_date = date.today()
    start_date = end_date - timedelta(days=2)
    in_range_day = start_date + timedelta(days=1)
    out_of_range_day = end_date + timedelta(days=3)
    csv_text = (
        "timestamp,open,high,low,close,volume\n"
        f"{in_range_day.isoformat()},100,110,95,105,1000\n"
        f"{out_of_range_day.isoformat()},105,112,101,110,1200\n"
    )
    executor = FakeExecutor([FakeResponse(200, text=csv_text)])
    adapter = _adapter(executor)
    frame = await adapter.get_historical_prices("AAPL", start_date, end_date)
    assert isinstance(frame, pl.DataFrame)
    assert frame.columns == ["timestamp", "open", "high", "low", "close", "volume"]
    assert frame.height == 1  # out_of_range_day is after end_date
    assert executor.calls[0][2]["params"]["outputsize"] == "compact"


async def test_old_start_date_requests_full_outputsize():
    """v1 always sent 'compact' → silently truncated history (review §4)."""
    csv_text = "timestamp,open,high,low,close,volume\n2026-01-02,100,110,95,105,1000\n"
    executor = FakeExecutor([FakeResponse(200, text=csv_text)])
    adapter = _adapter(executor)
    old_start = date.today() - timedelta(days=400)
    await adapter.get_historical_prices("AAPL", old_start, date.today())
    assert executor.calls[0][2]["params"]["outputsize"] == "full"


async def test_soft_rate_limit_feeds_aimd_and_raises():
    """HTTP-200 'Information: rate limit' payloads must back off AIMD (v1 never did)."""
    text = '{"Information": "API rate limit is 25 requests per day"}'
    executor = FakeExecutor([FakeResponse(200, text=text)])
    adapter = _adapter(executor)
    old_start = date.today() - timedelta(days=10)
    with pytest.raises(FinpipeRateLimitExceededError):
        await adapter.get_historical_prices("AAPL", old_start, date.today())
    assert executor.rate_limited_notes == 1


async def test_live_spot_price():
    """Alpha Vantage live spot price endpoint."""
    csv_text = "timestamp,open,high,low,close,volume\n2026-01-02,100,110,95,105,1000\n"
    executor = FakeExecutor([FakeResponse(200, text=csv_text)])
    adapter = _adapter(executor)
    frame = await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 3))
    assert isinstance(frame, pl.DataFrame)


async def test_invalid_ticker_raises_data_not_found():
    """Invalid ticker raises DataNotFoundError."""
    executor = FakeExecutor([FakeResponse(200, text='{"Error Message": "Invalid ticker"}')])
    adapter = _adapter(executor)
    with pytest.raises(FinpipeDataNotFoundError):
        await adapter.get_historical_prices("INVALID", date(2026, 1, 1), date(2026, 1, 3))


async def test_missing_timestamp_column_raises():
    """Missing timestamp column raises DataNotFoundError."""
    executor = FakeExecutor([FakeResponse(200, text='open,high,low,close\n100,110,95,105\n')])
    adapter = _adapter(executor)
    with pytest.raises(FinpipeDataNotFoundError):
        await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 3))


async def test_soft_rate_limit_with_frequency_keyword():
    """Soft rate limit with 'frequency' keyword also triggers."""
    text = '{"Information": "Thank you for using Alpha Vantage! frequency limit reached"}'
    executor = FakeExecutor([FakeResponse(200, text=text)])
    adapter = _adapter(executor)
    with pytest.raises(FinpipeRateLimitExceededError):
        await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 3))


async def test_intraday_interval_parameter():
    """Intraday interval is passed to API request."""
    csv_text = "timestamp,open,high,low,close,volume\n2026-01-02,100,110,95,105,1000\n"
    executor = FakeExecutor([FakeResponse(200, text=csv_text)])
    adapter = _adapter(executor)
    frame = await adapter.get_historical_prices("AAPL", date(2026, 1, 1), date(2026, 1, 3), interval="60min")
    assert isinstance(frame, pl.DataFrame)
    method, url, kwargs = executor.calls[0]
    assert kwargs["params"]["interval"] == "60min"
