from __future__ import annotations

import logging
import time
import traceback
from datetime import date, timedelta
from typing import TYPE_CHECKING

from finpipe.core.exceptions import (
    FinpipeConfigError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)
from finpipe.core.interfaces import (
    IHistoricalPriceProvider,
    ILLMProvider,
    IMacroProvider,
    IMarketIntelProvider,
    IMetadataProvider,
    IOptionsProvider,
    IScreenerProvider,
)
from finpipe.core.llm_compress import compress_llm_text_for_sentiment
from finpipe.core.models import SocialPostKind

if TYPE_CHECKING:
    from finpipe.client import Client

logger = logging.getLogger(__name__)


async def universal_probe_runner(client: Client, symbol: str, provider_id: str) -> str | None:
    """Universally run all supported endpoints for a provider based on its interfaces."""
    try:
        provider = client._registry.get(provider_id)
    except KeyError:
        return f"Provider {provider_id} not found in registry"

    errors = []
    end = date.today()
    start = end - timedelta(days=7)

    if isinstance(provider, IHistoricalPriceProvider):
        try:
            res = await provider.get_historical_prices(symbol, start, end)
            if hasattr(res, "is_empty") and res.is_empty():
                errors.append("get_historical_prices returned empty")
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_historical_prices failed: {e}")
            logger.error(f"{provider_id} get_historical_prices error:\n{traceback.format_exc()}")

    if isinstance(provider, IMetadataProvider):
        try:
            meta = await provider.get_metadata(symbol)
            if getattr(meta, "symbol", None) is None:
                errors.append("get_metadata missing symbol")
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_metadata failed: {e}")
            logger.error(f"{provider_id} get_metadata error:\n{traceback.format_exc()}")

        try:
            await provider.get_financial_statements(symbol)
        except NotImplementedError:
            pass
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_financial_statements failed: {e}")
            logger.error(f"{provider_id} get_financial_statements error:\n{traceback.format_exc()}")

    if isinstance(provider, IOptionsProvider):
        try:
            await provider.get_options_chain(symbol)
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_options_chain failed: {e}")
            logger.error(f"{provider_id} get_options_chain error:\n{traceback.format_exc()}")

        try:
            snap = await provider.get_options_snapshot(symbol, limit=1)
            if hasattr(snap, "is_empty") and snap.is_empty():
                errors.append("get_options_snapshot returned empty")
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_options_snapshot failed: {e}")
            logger.error(f"{provider_id} get_options_snapshot error:\n{traceback.format_exc()}")

    if isinstance(provider, IMacroProvider):
        try:
            res = await provider.get_macro_series("DGS10", start, end)
            if hasattr(res, "is_empty") and res.is_empty():
                errors.append("get_macro_series returned empty")
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_macro_series failed: {e}")
            logger.error(f"{provider_id} get_macro_series error:\n{traceback.format_exc()}")

    if isinstance(provider, IMarketIntelProvider):
        try:
            news = await provider.get_news(symbol, limit=1)
            if not news:
                errors.append("get_news returned empty")
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_news failed: {e}")
            logger.error(f"{provider_id} get_news error:\n{traceback.format_exc()}")

        try:
            posts = await provider.get_social_posts(symbol, limit=1, kind=SocialPostKind.MICROBLOG)
        except NotImplementedError:
            pass
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_social_posts (MICROBLOG) failed: {e}")
            logger.error(f"{provider_id} get_social_posts error:\n{traceback.format_exc()}")

        try:
            posts = await provider.get_social_posts(symbol, limit=1, kind=SocialPostKind.FORUM)
        except NotImplementedError:
            pass
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_social_posts (FORUM) failed: {e}")
            logger.error(f"{provider_id} get_social_posts error:\n{traceback.format_exc()}")

        try:
            await provider.get_sentiment_score(symbol)
        except NotImplementedError:
            pass
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"get_sentiment_score failed: {e}")
            logger.error(f"{provider_id} get_sentiment_score error:\n{traceback.format_exc()}")

    if isinstance(provider, IScreenerProvider):
        try:
            if hasattr(provider, "get_trending"):
                await provider.get_trending()
            elif hasattr(provider, "get_fundamental"):
                await provider.get_fundamental("geo_usa")
            elif hasattr(provider, "run_tradingview"):
                await provider.run_tradingview({"limit": 1, "filter": []})
            else:
                await provider.run_screener({})
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"screener execution failed: {e}")
            logger.error(f"{provider_id} screener error:\n{traceback.format_exc()}")

    if isinstance(provider, ILLMProvider):
        try:
            health = client.config.health
            prompt = f"{health.llm_probe_prompt} [{time.perf_counter():.6f}]"

            if provider_id == "gemini":
                resp = await provider.generate_response(
                    prompt,
                    generationConfig={"maxOutputTokens": health.llm_probe_max_tokens, "temperature": 0},
                )
            else:
                resp = await provider.generate_response(
                    prompt, max_tokens=health.llm_probe_max_tokens, temperature=0
                )

            if not resp.content or not str(resp.content).strip():
                errors.append("generate_response returned empty")
        except (FinpipeConfigError, FinpipeProviderDownError, FinpipeRateLimitExceededError):
            raise
        except Exception as e:
            errors.append(f"LLM generate_response failed: {e}")
            logger.error(f"{provider_id} LLM error:\n{traceback.format_exc()}")

    if errors:
        return " | ".join(errors)
    return None


def make_probe(provider_id: str):
    async def _probe(client: Client, symbol: str) -> str | None:
        return await universal_probe_runner(client, symbol, provider_id)
    return _probe


async def probe_compression_huggingface(client: Client, symbol: str) -> str | None:
    del symbol
    compression = client.config.llm_prompt.compression
    if not compression.endpoint_url:
        return "huggingface compression endpoint_url not configured"

    try:
        compressed = await compress_llm_text_for_sentiment(
            "This is a test sentence.",
            endpoint_url=compression.endpoint_url,
            target_ratio=0.5
        )
        if not compressed:
            return "huggingface compression returned empty"
        return None
    except Exception as exc:
        return f"huggingface compression failed: {exc}"


PROBE_RUNNERS = {
    "equity.yahoo": make_probe("yahoo"),
    "equity.alpha_vantage": make_probe("alpha_vantage"),
    "options.massive": make_probe("massive"),
    "options.yahoo": make_probe("yahoo"),
    "macro.fred": make_probe("fred"),
    "intel.google_news": make_probe("sentiment"),
    "intel.stocktwits": make_probe("sentiment"),
    "intel.reddit": make_probe("sentiment"),
    "screener.yahoo_trending": make_probe("screener"),
    "screener.yahoo_predefined": make_probe("screener"),
    "screener.finviz": make_probe("screener"),
    "screener.tradingview": make_probe("tradingview"),
    "llm.groq": make_probe("groq"),
    "llm.gemini": make_probe("gemini"),
    "llm.nvidia": make_probe("nvidia"),
    "compression.huggingface": probe_compression_huggingface,
}
