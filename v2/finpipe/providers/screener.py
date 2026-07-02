"""Unified screener adapter: Yahoo trending/predefined, Finviz, TradingView.

v2 fixes vs v1:
- ONE error contract: every source raises classified finpipe errors (v1 mixed
  silent ``[]`` returns with raises — callers couldn't tell "no matches" from
  "provider down"). Callers wanting soft behavior catch ``FinpipeError``.
- per-source executors via ``runtime.executor_factory``; leaf namespaces match
  the hard-cap table (``screener.finviz`` → ``finviz``)
- parsers stay in this package (they're vendor logic, not `core`)
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..core.config import ScreenerConfig, SourceConfig
from ..runtime.resilience import RequestExecutor
from .base import ProviderAdapter, ProviderRuntime
from .manifest import provider

logger = logging.getLogger(__name__)

_YAHOO_TRENDING_URL = "https://query1.finance.yahoo.com/v1/finance/trending/US"
_YAHOO_PREDEFINED_URL = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
_TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/america/scan"
_DEFAULT_PREDEFINED_LIMIT = 50
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
_FINVIZ_TICKER_RE = re.compile(r"(?:quote\.ashx\?t=|/stock/(?:quote/)?)([A-Z]{1,5})\b")


# --------------------------------------------------------------------------- parsers
def parse_yahoo_trending_symbols(payload: dict[str, Any]) -> list[str]:
    quotes = ((payload.get("finance") or {}).get("result") or [{}])[0].get("quotes", [])
    symbols = [str(q.get("symbol", "")).upper() for q in quotes]
    return [s for s in symbols if _TICKER_RE.match(s)]


def parse_yahoo_quote_payload(payload: dict[str, Any]) -> list[str]:
    result = ((payload.get("finance") or {}).get("result") or [{}])[0]
    quotes = result.get("quotes", [])
    symbols = {str(q.get("symbol", "")).upper() for q in quotes}
    return [s for s in symbols if _TICKER_RE.match(s)]


def parse_finviz_screener_tickers(html_text: str) -> list[str]:
    return sorted({m.group(1) for m in _FINVIZ_TICKER_RE.finditer(html_text)})


def parse_tradingview_scan_symbols(payload: dict[str, Any]) -> list[str]:
    symbols = []
    for row in payload.get("data", []):
        raw = str(row.get("s", ""))
        symbols.append(raw.split(":", 1)[-1] if ":" in raw else raw)
    return symbols


# --------------------------------------------------------------------------- adapter
class ScreenerAdapter(ProviderAdapter):
    def __init__(self, runtime: ProviderRuntime) -> None:
        super().__init__(runtime)
        self._config: ScreenerConfig = runtime.config
        self._executors: dict[str, RequestExecutor] = {}

    def _source(self, name: str) -> SourceConfig:
        return getattr(self._config.sources, name)

    def _executor_for(self, name: str) -> RequestExecutor | None:
        source = self._source(name)
        if not self._config.enabled or not source.enabled:
            return None
        executor = self._executors.get(name)
        if executor is None:
            assert self._rt.executor_factory is not None
            executor = self._rt.executor_factory(f"screener.{name}", source.rate_limits, source.http)
            self._executors[name] = executor
        return executor

    def _headers_for(self, name: str) -> dict[str, str] | None:
        source = self._source(name)
        headers: dict[str, str] = {}
        if source.http.user_agent:
            headers["User-Agent"] = source.http.user_agent
        elif name.startswith("yahoo"):
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        if name == "finviz":
            headers["Referer"] = "https://finviz.com/screener.ashx"
            headers["Accept"] = "text/html,application/xhtml+xml"
        return headers or None

    async def close(self) -> None:
        for executor in self._executors.values():
            await executor.close()
        await self._rt.executor.close()

    async def describe(self) -> dict[str, Any]:
        from ..observe.describe import provider_descriptor, settings_snapshot

        sources = {
            name: settings_snapshot(self._source(name))
            for name in ("yahoo_trending", "yahoo_predefined", "finviz", "tradingview")
        }
        return provider_descriptor("screener", "screener", self._config, details={"sources": sources})

    # -- sources ------------------------------------------------------------------
    async def get_trending(self) -> list[str]:
        executor = self._executor_for("yahoo_trending")
        if executor is None:
            return []

        async def fetch() -> list[str]:
            response = await executor.request(
                "GET", _YAHOO_TRENDING_URL, headers=self._headers_for("yahoo_trending")
            )
            return parse_yahoo_trending_symbols(response.json())

        return await self.cached_fetch(
            "yahoo_trending", ("trending",), self._config.source_fetch_ttl("yahoo_trending"), fetch
        )

    async def get_predefined(self, scr_id: str, *, limit: int | None = None) -> list[str]:
        executor = self._executor_for("yahoo_predefined")
        if executor is None:
            return []
        source = self._source("yahoo_predefined")
        resolved = limit if limit is not None else (source.default_limit or _DEFAULT_PREDEFINED_LIMIT)

        async def fetch() -> list[str]:
            url = (
                f"{_YAHOO_PREDEFINED_URL}?formatted=false&lang=en-US&region=US"
                f"&scrIds={scr_id}&count={resolved}"
            )
            response = await executor.request("GET", url, headers=self._headers_for("yahoo_predefined"))
            return sorted(parse_yahoo_quote_payload(response.json()))

        return await self.cached_fetch(
            "yahoo_predefined", (scr_id, resolved),
            self._config.source_fetch_ttl("yahoo_predefined"), fetch,
        )

    async def get_fundamental(self, filter_key: str) -> list[str]:
        executor = self._executor_for("finviz")
        if executor is None:
            return []

        async def fetch() -> list[str]:
            url = f"https://finviz.com/screener.ashx?v=111&s={filter_key}"
            response = await executor.request("GET", url, headers=self._headers_for("finviz"))
            return parse_finviz_screener_tickers(response.text)

        return await self.cached_fetch(
            "finviz", (filter_key,), self._config.source_fetch_ttl("finviz"), fetch
        )

    async def run_tradingview(self, criteria: dict[str, Any]) -> list[str]:
        executor = self._executor_for("tradingview")
        if executor is None:
            return []

        async def fetch() -> list[str]:
            payload = {
                "filter": criteria.get("filter", []),
                "options": {"lang": "en"},
                "markets": criteria.get("markets", ["america"]),
                "symbols": {"query": {"types": []}, "tickers": []},
                "columns": ["name"],
                "sort": criteria.get("sort", {"sortBy": "volume", "sortOrder": "desc"}),
                "range": [0, criteria.get("limit", 150)],
            }
            response = await executor.request(
                "POST", _TRADINGVIEW_SCAN_URL, json=payload,
                headers={"Content-Type": "application/json"},
            )
            return parse_tradingview_scan_symbols(response.json())

        canonical = tuple(sorted((k, str(v)) for k, v in criteria.items()))
        return await self.cached_fetch(
            "tradingview", tuple("=".join(pair) for pair in canonical),
            self._config.source_fetch_ttl("tradingview"), fetch,
        )

    # -- IScreenerProvider + dispatch --------------------------------------------------
    async def run_screener(self, criteria: dict[str, Any]) -> list[str]:
        return await self.run_tradingview(criteria)

    async def run(self, source: str, **params: Any) -> list[str]:
        if source == "yahoo_trending":
            return await self.get_trending()
        if source == "yahoo_predefined":
            return await self.get_predefined(params["scr_id"], limit=params.get("limit"))
        if source == "finviz":
            return await self.get_fundamental(params["filter_key"])
        if source == "tradingview":
            return await self.run_tradingview(params.get("criteria", params))
        raise ValueError(f"Unknown screener source: {source}")


@provider(
    "screener",
    capability="screener",
    config_attr="screener",
    label="Market Screeners",
    description="Yahoo trending/predefined, Finviz fundamentals, TradingView scans",
    probe="screener.screener",
)
def build_screener(runtime: ProviderRuntime) -> ScreenerAdapter:
    return ScreenerAdapter(runtime)
