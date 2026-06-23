"""Pure parse helpers for screener HTTP payloads."""

from __future__ import annotations

import re
from typing import Any

_FINVIZ_TICKER_PATTERN = re.compile(r"(?:quote\.ashx\?t|stock\?t)=([A-Z]{1,5})")
_MAX_TRENDING_TICKER_LENGTH = 5


def parse_finviz_screener_tickers(html: str) -> set[str]:
    """Extract tickers from Finviz screener HTML (stock?t= and legacy quote.ashx?t=)."""
    return set(_FINVIZ_TICKER_PATTERN.findall(html))


def parse_yahoo_quote_payload(data: Any) -> set[str]:
    """Extract symbols from Yahoo finance quote/screener JSON payloads."""
    if not isinstance(data, dict):
        return set()
    quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
    return {symbol for q in quotes if (symbol := q.get("symbol"))}


def parse_yahoo_trending_symbols(data: Any) -> list[str]:
    """Extract alpha US equity tickers (1-5 chars) from Yahoo trending JSON."""
    if not isinstance(data, dict):
        return []
    quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
    symbols: list[str] = []
    for quote in quotes:
        symbol = quote.get("symbol")
        if (
            isinstance(symbol, str)
            and symbol.isalpha()
            and 1 <= len(symbol) <= _MAX_TRENDING_TICKER_LENGTH
        ):
            symbols.append(symbol.upper())
    return symbols


def parse_tradingview_scan_symbols(data: Any) -> list[str]:
    """Extract ticker symbols from TradingView scanner JSON."""
    if not isinstance(data, dict):
        return []
    matches: list[str] = []
    for item in data.get("data", []):
        ticker_raw = item.get("d", [None])[0]
        if ticker_raw:
            symbol = ticker_raw.split(":")[-1] if ":" in ticker_raw else ticker_raw
            matches.append(symbol)
    return matches
