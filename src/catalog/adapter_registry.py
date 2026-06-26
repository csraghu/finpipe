"""Internal adapter construction and lifecycle (one instance per adapter key in v1)."""

from __future__ import annotations

import logging
from typing import Any

from finpipe.core.config import FinpipeConfig
from finpipe.providers.alpha_vantage import AlphaVantageAdapter
from finpipe.providers.fred import FredAdapter
from finpipe.providers.gemini import GeminiAdapter
from finpipe.providers.groq import GroqAdapter
from finpipe.providers.massive import MassiveOptionsAdapter
from finpipe.providers.nvidia import NvidiaAdapter
from finpipe.providers.screener import ScreenerAdapter
from finpipe.providers.sentiment import NewsSentimentAdapter
from finpipe.providers.tradingview import TradingViewAdapter
from finpipe.providers.yahoo import YahooFinanceAdapter

logger = logging.getLogger(__name__)

_ADAPTER_KEYS = (
    "yahoo",
    "alpha_vantage",
    "massive",
    "fred",
    "screener",
    "tradingview",
    "sentiment",
    "groq",
    "gemini",
    "nvidia",
)


class AdapterRegistry:
    def __init__(self, config: FinpipeConfig) -> None:
        self._config = config
        self._adapters: dict[str, Any] = {}
        self._build()

    def _build(self) -> None:
        screener = ScreenerAdapter(self._config)
        self._adapters = {
            "yahoo": YahooFinanceAdapter(self._config),
            "alpha_vantage": AlphaVantageAdapter(self._config),
            "massive": MassiveOptionsAdapter(self._config),
            "fred": FredAdapter(self._config),
            "screener": screener,
            "tradingview": TradingViewAdapter(self._config, screener=screener),
            "sentiment": NewsSentimentAdapter(self._config),
            "groq": GroqAdapter(self._config),
            "gemini": GeminiAdapter(self._config),
            "nvidia": NvidiaAdapter(self._config),
        }

    def get(self, name: str) -> Any:
        try:
            return self._adapters[name]
        except KeyError as exc:
            raise KeyError(f"Unknown adapter key: {name}") from exc

    def equity_adapters(self) -> dict[str, Any]:
        return {key: self._adapters[key] for key in ("yahoo", "alpha_vantage")}

    def options_adapters(self) -> dict[str, Any]:
        return {key: self._adapters[key] for key in ("massive", "yahoo")}

    def keys(self) -> tuple[str, ...]:
        return _ADAPTER_KEYS

    async def close(self) -> None:
        if not self._config.cache.singleton:
            closed_cache_ids: set[int] = set()
            for adapter in self._adapters.values():
                cache = getattr(adapter, "_cache", None)
                if cache is not None and id(cache) not in closed_cache_ids:
                    cache.close()
                    closed_cache_ids.add(id(cache))

        for key in (
            "alpha_vantage",
            "massive",
            "fred",
            "screener",
            "sentiment",
            "groq",
            "gemini",
            "nvidia",
        ):
            adapter = self._adapters.get(key)
            if adapter is not None:
                await adapter.close()
        tradingview = self._adapters.get("tradingview")
        if tradingview is not None:
            await tradingview.close()
        logger.info("Finpipe adapter registry shut down.")
