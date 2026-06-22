import logging
from typing import Any

from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.core.interfaces import IScreenerProvider
from finpipe.core.registry import BuildContext, register_provider
from finpipe.network.resilience import create_resilient_http_client

logger = logging.getLogger(__name__)


class TradingViewAdapter(IScreenerProvider):
    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._client = create_resilient_http_client(
            "screener.tradingview",
            config.providers.tradingview.rate_limits,
            cache_config=config.cache,
        )
        self._base_url = "https://scanner.tradingview.com/america/scan"

    async def close(self) -> None:
        await self._client.close()

    async def run_screener(self, criteria: dict[str, Any]) -> list[str]:
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
            response = await self._client.request(
                "POST", self._base_url, json=payload, headers={"Content-Type": "application/json"}
            )
            data = response.json()
        except Exception as exc:
            logger.error("TradingView screener failed: %s", exc)
            raise FinpipeProviderDownError("Failed to fetch data from TradingView screener") from exc

        matches: list[str] = []
        for item in data.get("data", []):
            ticker_raw = item.get("d", [None])[0]
            if ticker_raw:
                symbol = ticker_raw.split(":")[-1] if ":" in ticker_raw else ticker_raw
                matches.append(symbol)
        return matches


@register_provider("tradingview", category="screener")
def build_tradingview(ctx: BuildContext) -> TradingViewAdapter:
    return TradingViewAdapter(ctx.config)
