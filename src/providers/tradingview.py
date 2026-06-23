import logging
from typing import Any

from finpipe.core.config import FinpipeConfig
from finpipe.core.interfaces import IScreenerProvider
from finpipe.core.registry import BuildContext, register_provider
from finpipe.providers.screener import ScreenerAdapter

logger = logging.getLogger(__name__)


class TradingViewAdapter(IScreenerProvider):
    """Backward-compatible TradingView screener; delegates to unified ScreenerAdapter."""

    def __init__(self, config: FinpipeConfig, *, screener: ScreenerAdapter | None = None):
        self._config = config
        self._screener = screener or ScreenerAdapter(config)

    async def close(self) -> None:
        if self._screener is not None:
            await self._screener.close()

    async def run_screener(self, criteria: dict[str, Any]) -> list[str]:
        return await self._screener.run_tradingview(criteria)


@register_provider("tradingview", category="screener")
def build_tradingview(ctx: BuildContext) -> TradingViewAdapter:
    return TradingViewAdapter(ctx.config)
