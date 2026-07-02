"""finpipe v2 — unified, resilient financial data pipeline (rearchitecture).

Public API (semver surface once packaged):

    from finpipe import Client, FinpipeConfig
    async with Client(FinpipeConfig.load()) as client:
        prices = await client.equity.get_historical_prices("AAPL", start, end)
"""

from .client import Client
from .core.config import FinpipeConfig
from .core.errors import (
    FinpipeAuthError,
    FinpipeConfigError,
    FinpipeDataNotFoundError,
    FinpipeError,
    FinpipeParseError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)
from .core.models import (
    LLMResponse,
    NewsArticle,
    OptionChain,
    OptionContract,
    SentimentScore,
    SocialPost,
    SocialPostKind,
    TickerMetadata,
)

__version__ = "2.0.0a0"

__all__ = [
    "Client",
    "FinpipeConfig",
    "FinpipeError",
    "FinpipeAuthError",
    "FinpipeConfigError",
    "FinpipeDataNotFoundError",
    "FinpipeParseError",
    "FinpipeProviderDownError",
    "FinpipeRateLimitExceededError",
    "LLMResponse",
    "NewsArticle",
    "OptionChain",
    "OptionContract",
    "SentimentScore",
    "SocialPost",
    "SocialPostKind",
    "TickerMetadata",
]
