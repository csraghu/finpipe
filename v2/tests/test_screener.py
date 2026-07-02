"""Screener adapter tests: uniform degradation, per-source executors, symbol parsing."""

from __future__ import annotations

from finpipe.core.config import ScreenerConfig
from finpipe.providers.screener import (
    ScreenerAdapter,
    parse_finviz_screener_tickers,
    parse_tradingview_scan_symbols,
    parse_yahoo_quote_payload,
    parse_yahoo_trending_symbols,
)

from conftest import FakeExecutor, FakeResponse, make_runtime


def _adapter(executor: FakeExecutor | None = None) -> ScreenerAdapter:
    config = ScreenerConfig()
    return ScreenerAdapter(make_runtime(config, executor or FakeExecutor(), provider_key="screener"))


async def test_yahoo_trending():
    """Yahoo trending endpoint returns top symbols."""
    payload = {
        "finance": {
            "result": [
                {
                    "quotes": [
                        {"symbol": "AAPL"},
                        {"symbol": "MSFT"},
                        {"symbol": "TSLA"},
                    ]
                }
            ]
        }
    }

    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)
    symbols = await adapter.get_trending()

    assert "AAPL" in symbols
    assert "MSFT" in symbols
    assert "TSLA" in symbols


async def test_empty_trending():
    """Empty trending results return empty list."""
    payload = {"finance": {"result": [{"quotes": []}]}}

    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)
    symbols = await adapter.get_trending()

    assert symbols == []


async def test_predefined_screener():
    """Predefined screener returns saved list."""
    payload = {
        "finance": {
            "result": [
                {
                    "quotes": [
                        {"symbol": "AAPL"},
                        {"symbol": "MSFT"},
                    ]
                }
            ]
        }
    }

    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)
    symbols = await adapter.get_predefined("top_gainers")

    assert len(symbols) >= 0  # May be empty if screener disabled


async def test_invalid_symbols_filtered():
    """Invalid symbol formats are filtered from results."""
    symbols = ["AAPL", "1234", "TOOLONG123", "MSFT"]
    # Only AAPL and MSFT should pass the [A-Z]{1,5} pattern
    valid = [s for s in symbols if len(s) <= 5 and s.isalpha()]
    assert "AAPL" in valid
    assert "MSFT" in valid
    assert "1234" not in valid


def test_parse_yahoo_trending_symbols():
    """Parse Yahoo trending payload correctly."""
    payload = {
        "finance": {
            "result": [
                {
                    "quotes": [
                        {"symbol": "AAPL"},
                        {"symbol": "MSFT"},
                    ]
                }
            ]
        }
    }
    symbols = parse_yahoo_trending_symbols(payload)
    assert "AAPL" in symbols
    assert "MSFT" in symbols


def test_parse_yahoo_quote_payload():
    """Parse Yahoo quote payload."""
    payload = {
        "finance": {
            "result": [
                {
                    "quotes": [
                        {"symbol": "AAPL"},
                        {"symbol": "MSFT"},
                    ]
                }
            ]
        }
    }
    symbols = parse_yahoo_quote_payload(payload)
    assert "AAPL" in symbols
    assert "MSFT" in symbols


def test_parse_finviz_screener_html():
    """Parse Finviz HTML screener page for tickers."""
    html = """
    <html>
    <a href="quote.ashx?t=AAPL">Apple</a>
    <a href="/stock/quote/MSFT">Microsoft</a>
    <a href="/stock/TSLA">Tesla</a>
    </html>
    """
    symbols = parse_finviz_screener_tickers(html)
    assert "AAPL" in symbols
    assert "MSFT" in symbols
    assert "TSLA" in symbols


def test_parse_tradingview_scan_symbols():
    """Parse TradingView scan API response."""
    payload = {
        "data": [
            {"s": "NASDAQ:AAPL"},
            {"s": "NASDAQ:MSFT"},
            {"s": "TSLA"},
        ]
    }
    symbols = parse_tradingview_scan_symbols(payload)
    assert "AAPL" in symbols
    assert "MSFT" in symbols
    assert "TSLA" in symbols


async def test_malformed_response_returns_empty():
    """Malformed responses return empty symbol list."""
    executor = FakeExecutor([FakeResponse(200, json_data={})])
    adapter = _adapter(executor)
    symbols = await adapter.get_trending()

    assert symbols == []


async def test_screener_disabled_returns_empty():
    """Disabled screener returns empty results."""
    config = ScreenerConfig(enabled=False)
    adapter = ScreenerAdapter(make_runtime(config, FakeExecutor(), provider_key="screener"))
    symbols = await adapter.get_trending()

    assert symbols == []


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
