import logging
from typing import Any

from finpipe.core.config import (
    FinpipeConfig,
    ScreenerSourceConfig,
    resolve_screener_tradingview_source,
)
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.core.interfaces import IProviderDescribe
from finpipe.core.registry import BuildContext, register_provider
from finpipe.core.screener_parsers import (
    parse_finviz_screener_tickers,
    parse_tradingview_scan_symbols,
    parse_yahoo_quote_payload,
    parse_yahoo_trending_symbols,
)
from finpipe.network.cache import create_cache_backend
from finpipe.network.resilience import ResilientHttpClient, create_resilient_http_client
from finpipe.providers.descriptor import provider_descriptor, settings_snapshot

logger = logging.getLogger(__name__)

_YAHOO_TRENDING_URL = "https://query1.finance.yahoo.com/v1/finance/trending/US"
_YAHOO_PREDEFINED_URL = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
_TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/america/scan"
_DEFAULT_PREDEFINED_LIMIT = 50


class ScreenerAdapter(IProviderDescribe):
    """Unified screener adapter dispatching to Yahoo, Finviz, and TradingView sources."""

    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._provider_config = config.providers.screener
        self._cache = create_cache_backend(config.cache)
        self._clients: dict[str, ResilientHttpClient] = {
            name: create_resilient_http_client(
                f"screener.{name}",
                source.rate_limits,
                cache_config=config.cache,
            )
            for name, source in self._source_configs().items()
        }

    async def describe(self) -> dict[str, Any]:
        sources = {
            name: settings_snapshot(source) for name, source in self._source_configs().items()
        }
        return provider_descriptor(
            provider_id="screener",
            capability="screener",
            provider_config=self._provider_config,
            details={"sources": sources},
        )

    def _source_configs(self) -> dict[str, ScreenerSourceConfig]:
        sources = self._provider_config.sources
        return {
            "yahoo_trending": sources.yahoo_trending,
            "yahoo_predefined": sources.yahoo_predefined,
            "finviz": sources.finviz,
            "tradingview": resolve_screener_tradingview_source(
                self._provider_config,
                self._config.providers.tradingview,
            ),
        }

    def _source_cache_key(self, source_name: str, suffix: str) -> str:
        return f"screener_src_{source_name}_{suffix}"

    def _client_for(self, source_name: str) -> ResilientHttpClient | None:
        if not self._provider_config.enabled:
            return None
        source = self._source_configs().get(source_name)
        if source is None or not source.enabled:
            return None
        return self._clients[source_name]

    def _headers_for(self, source_name: str) -> dict[str, str]:
        source = self._source_configs()[source_name]
        headers: dict[str, str] = {}
        if source.http.user_agent:
            headers["User-Agent"] = source.http.user_agent
        elif source_name in ("yahoo_trending", "yahoo_predefined", "finviz"):
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        return headers

    def _fetch_ttl(self, source_name: str) -> int:
        return self._provider_config.resolve_source_fetch_ttl(
            source_name,
            legacy_tradingview=self._config.providers.tradingview.ttls,
        )

    async def close(self) -> None:
        for client in self._clients.values():
            await client.close()

    async def get_trending(self) -> list[str]:
        client = self._client_for("yahoo_trending")
        if client is None:
            return []

        cache_key = self._source_cache_key("yahoo_trending", "trending")
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)

        try:
            response = await client.request(
                "GET", _YAHOO_TRENDING_URL, headers=self._headers_for("yahoo_trending") or None
            )
            symbols = parse_yahoo_trending_symbols(response.json())
            self._cache.set(cache_key, symbols, self._fetch_ttl("yahoo_trending"))
            return symbols
        except Exception as exc:
            logger.warning("Yahoo trending screener failed: %s", exc)
            return []

    async def get_predefined(self, scr_id: str, *, limit: int | None = None) -> list[str]:
        client = self._client_for("yahoo_predefined")
        if client is None:
            return []

        source = self._provider_config.sources.yahoo_predefined
        resolved_limit = (
            limit if limit is not None else (source.default_limit or _DEFAULT_PREDEFINED_LIMIT)
        )
        cache_key = self._source_cache_key("yahoo_predefined", f"{scr_id}_{resolved_limit}")
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)

        url = (
            f"{_YAHOO_PREDEFINED_URL}"
            f"?formatted=false&lang=en-US&region=US&scrIds={scr_id}&count={resolved_limit}"
        )
        try:
            response = await client.request(
                "GET", url, headers=self._headers_for("yahoo_predefined") or None
            )
            symbols = sorted(parse_yahoo_quote_payload(response.json()))
            self._cache.set(cache_key, symbols, self._fetch_ttl("yahoo_predefined"))
            return symbols
        except Exception as exc:
            logger.warning("Yahoo predefined screener %s failed: %s", scr_id, exc)
            return []

    async def get_fundamental(self, filter_key: str) -> list[str]:
        client = self._client_for("finviz")
        if client is None:
            return []

        cache_key = self._source_cache_key("finviz", filter_key)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)

        url = f"https://finviz.com/screener.ashx?v=111&s={filter_key}"
        try:
            response = await client.request("GET", url, headers=self._headers_for("finviz") or None)
            symbols = sorted(parse_finviz_screener_tickers(response.text))
            self._cache.set(cache_key, symbols, self._fetch_ttl("finviz"))
            return symbols
        except Exception as exc:
            logger.warning("Finviz screener %s failed: %s", filter_key, exc)
            return []

    async def run_tradingview(self, criteria: dict[str, Any]) -> list[str]:
        client = self._client_for("tradingview")
        if client is None:
            return []

        cache_key = self._source_cache_key("tradingview", str(sorted(criteria.items())))
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)

        payload = {
            "filter": criteria.get("filter", []),
            "options": {"lang": "en"},
            "markets": criteria.get("markets", ["america"]),
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": ["name"],
            "sort": criteria.get(
                "sort",
                {"sortBy": "volume", "sortOrder": "desc"},
            ),
            "range": [0, criteria.get("limit", 150)],
        }
        try:
            response = await client.request(
                "POST",
                _TRADINGVIEW_SCAN_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            symbols = parse_tradingview_scan_symbols(response.json())
            self._cache.set(cache_key, symbols, self._fetch_ttl("tradingview"))
            return symbols
        except Exception as exc:
            logger.error("TradingView screener failed: %s", exc)
            raise FinpipeProviderDownError(
                "Failed to fetch data from TradingView screener"
            ) from exc

    async def run(self, source: str, **params: Any) -> list[str]:
        if source == "yahoo_trending":
            return await self.get_trending()
        if source == "yahoo_predefined":
            return await self.get_predefined(
                params["scr_id"],
                limit=params.get("limit"),
            )
        if source == "finviz":
            return await self.get_fundamental(params["filter_key"])
        if source == "tradingview":
            return await self.run_tradingview(params.get("criteria", params))
        raise ValueError(f"Unknown screener source: {source}")


@register_provider("screener", category="screener")
def build_screener(ctx: BuildContext) -> ScreenerAdapter:
    return ScreenerAdapter(ctx.config)
