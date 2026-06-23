import logging
from typing import Any, Self

from finpipe.core.config import FinpipeConfig
from finpipe.providers.alpha_vantage import AlphaVantageAdapter
from finpipe.providers.composite import (
    CompositeEquityService,
    CompositeIntelService,
    CompositeLlmService,
    CompositeMacroService,
    CompositeOptionsService,
    CompositeScreenerService,
)
from finpipe.providers.fred import FredAdapter
from finpipe.providers.gemini import GeminiAdapter
from finpipe.providers.groq import GroqAdapter
from finpipe.providers.massive import MassiveOptionsAdapter
from finpipe.providers.sentiment import NewsSentimentAdapter
from finpipe.providers.screener import ScreenerAdapter
from finpipe.providers.tradingview import TradingViewAdapter
from finpipe.providers.yahoo import YahooFinanceAdapter

logger = logging.getLogger(__name__)


class Client:
    """Top-level facade. Prefer capability services (equity, options, intel, …)."""

    def __init__(self, config: FinpipeConfig | None = None):
        self.config = config or FinpipeConfig.load()
        self._ensure_registrations()

        self.yahoo = YahooFinanceAdapter(self.config)
        self.alpha_vantage = AlphaVantageAdapter(self.config)
        self.massive = MassiveOptionsAdapter(self.config)
        self.fred = FredAdapter(self.config)
        self._screener_adapter = ScreenerAdapter(self.config)
        self.tradingview = TradingViewAdapter(self.config, screener=self._screener_adapter)
        self.sentiment = NewsSentimentAdapter(self.config)
        self.groq = GroqAdapter(self.config)
        self.gemini = GeminiAdapter(self.config)

        equity_adapters = {
            "yahoo": self.yahoo,
            "alpha_vantage": self.alpha_vantage,
        }
        options_adapters = {
            "massive": self.massive,
            "yahoo": self.yahoo,
        }
        self.options = CompositeOptionsService(self.config, adapters=options_adapters)
        self.equity = CompositeEquityService(
            self.config,
            adapters=equity_adapters,
            options=self.options,
        )
        self.macro = CompositeMacroService(self.config)
        self.intel = CompositeIntelService(self.config, sentiment=self.sentiment)
        self.screener = CompositeScreenerService(self.config, screener=self._screener_adapter)
        self.llm = CompositeLlmService(self.config)

    @staticmethod
    def _ensure_registrations() -> None:
        import finpipe.providers  # noqa: F401 — side-effect registration

    async def close(self) -> None:
        await self.alpha_vantage.close()
        await self.massive.close()
        await self.fred.close()
        await self._screener_adapter.close()
        await self.sentiment.close()
        await self.groq.close()
        await self.gemini.close()
        logger.info("Finpipe client gracefully shut down.")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    def dump_settings(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        """Return resolved settings for all capability and provider interfaces."""
        return self.config.dump_settings(redact_secrets=redact_secrets)

    def dump_settings_json(self, *, indent: int = 2, redact_secrets: bool = True) -> str:
        """Serialize resolved settings for all capability and provider interfaces."""
        return self.config.dump_settings_json(indent=indent, redact_secrets=redact_secrets)
