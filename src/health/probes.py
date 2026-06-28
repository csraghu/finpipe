from __future__ import annotations

import time
from datetime import date, timedelta
from typing import TYPE_CHECKING

from finpipe.core.exceptions import FinpipeConfigError
from finpipe.core.models import SocialPostKind

if TYPE_CHECKING:
    from finpipe.client import Client


async def probe_equity_yahoo(client: Client, symbol: str) -> str | None:
    meta = await client.catalog.capability("equity").provider("yahoo").get_metadata(symbol)
    if not meta.symbol:
        return "metadata missing symbol"
    return None


async def probe_equity_alpha_vantage(client: Client, symbol: str) -> str | None:
    if not client.config.providers.alpha_vantage.api_key:
        raise FinpipeConfigError("ALPHA_VANTAGE_API_KEY not configured")
    av = client.catalog.capability("equity").provider("alpha_vantage")
    price = await av.get_live_spot_price(symbol)
    if price is None:
        return f"spot price unavailable for {symbol}"
    return None


async def probe_options_massive(client: Client, symbol: str) -> str | None:
    if not client.config.providers.massive.api_key:
        raise FinpipeConfigError("MASSIVE_API_KEY not configured")
    massive = client.catalog.capability("options").provider("massive")
    snapshots = await massive.fetch_options_snapshot(symbol, limit=1)
    if not snapshots:
        return "options snapshot empty"
    return None


async def probe_options_yahoo(client: Client, symbol: str) -> str | None:
    frame = (
        await client.catalog.capability("options")
        .provider("yahoo")
        .get_options_snapshot(symbol, limit=1)
    )
    if frame is None or (hasattr(frame, "is_empty") and frame.is_empty()):
        return "options snapshot empty"
    return None


async def probe_macro_fred(client: Client, symbol: str) -> str | None:
    del symbol
    if not client.config.providers.fred.api_key:
        raise FinpipeConfigError("FRED_API_KEY not configured")
    end = date.today()
    start = end - timedelta(days=7)
    series = (
        await client.catalog.capability("macro")
        .provider("fred")
        .get_macro_series("DGS10", start, end)
    )
    if series is None or (hasattr(series, "is_empty") and series.is_empty()):
        return "macro series empty"
    return None


async def probe_intel_google_news(client: Client, symbol: str) -> str | None:
    articles = await client.catalog.capability("intel").get_news(symbol, limit=1)
    if not articles:
        return "no news articles returned"
    return None


async def probe_intel_stocktwits(client: Client, symbol: str) -> str | None:
    posts = await client.catalog.capability("intel").get_social_posts(
        symbol, limit=1, kind=SocialPostKind.MICROBLOG
    )
    if not posts:
        return "no stocktwits posts returned"
    return None


async def probe_intel_reddit(client: Client, symbol: str) -> str | None:
    del symbol
    reddit_symbol = client.config.health.reddit_probe_symbol
    posts = await client.catalog.capability("intel").get_social_posts(
        reddit_symbol, limit=1, kind=SocialPostKind.FORUM
    )
    if not posts:
        return f"no reddit posts returned for {reddit_symbol}"
    return None


async def probe_screener_yahoo_trending(client: Client, symbol: str) -> str | None:
    del symbol
    tickers = await client.catalog.capability("screener").get_trending()
    if not tickers:
        return "trending screener returned no tickers"
    return None


async def probe_screener_yahoo_predefined(client: Client, symbol: str) -> str | None:
    del symbol
    tickers = await client.catalog.capability("screener").get_predefined("day_gainers", limit=5)
    if not tickers:
        return "predefined screener returned no tickers"
    return None


async def probe_screener_finviz(client: Client, symbol: str) -> str | None:
    del symbol
    health = client.config.health
    screener = client.catalog.capability("screener")
    filters: list[str] = []
    for key in (health.finviz_probe_filter, "geo_usa", "ta_topgainers"):
        if key not in filters:
            filters.append(key)
    for filter_key in filters:
        tickers = await screener.get_fundamental(filter_key)
        if tickers:
            return None
    return f"finviz screener returned no tickers (tried: {', '.join(filters)})"


async def probe_screener_tradingview(client: Client, symbol: str) -> str | None:
    del symbol
    tickers = await client.catalog.capability("screener").run_tradingview(
        {"limit": 1, "filter": []}
    )
    if not tickers:
        return "tradingview screener returned no tickers"
    return None


async def _probe_llm_provider(client: Client, provider_id: str) -> str | None:
    """Run a minimal chat completion against an LLM provider (HTTP 200 semantics)."""
    provider_cfg = getattr(client.config.providers, provider_id)
    api_key = getattr(provider_cfg, "api_key", None)
    if not api_key:
        env_name = provider_id.upper() + "_API_KEY"
        raise FinpipeConfigError(f"{env_name} not configured")

    health = client.config.health
    prompt = f"{health.llm_probe_prompt} [{time.perf_counter():.6f}]"
    provider = client.catalog.capability("llm").provider(provider_id)

    if provider_id == "gemini":
        response = await provider.generate_response(
            prompt,
            generationConfig={
                "maxOutputTokens": health.llm_probe_max_tokens,
                "temperature": 0,
            },
        )
    else:
        response = await provider.generate_response(
            prompt,
            max_tokens=health.llm_probe_max_tokens,
            temperature=0,
        )

    if not response.content or not str(response.content).strip():
        return f"{provider_id} returned empty completion"
    return None


async def probe_llm_groq(client: Client, symbol: str) -> str | None:
    del symbol
    return await _probe_llm_provider(client, "groq")


async def probe_llm_gemini(client: Client, symbol: str) -> str | None:
    del symbol
    return await _probe_llm_provider(client, "gemini")


async def probe_llm_nvidia(client: Client, symbol: str) -> str | None:
    del symbol
    return await _probe_llm_provider(client, "nvidia")


PROBE_RUNNERS = {
    "equity.yahoo": probe_equity_yahoo,
    "equity.alpha_vantage": probe_equity_alpha_vantage,
    "options.massive": probe_options_massive,
    "options.yahoo": probe_options_yahoo,
    "macro.fred": probe_macro_fred,
    "intel.google_news": probe_intel_google_news,
    "intel.stocktwits": probe_intel_stocktwits,
    "intel.reddit": probe_intel_reddit,
    "screener.yahoo_trending": probe_screener_yahoo_trending,
    "screener.yahoo_predefined": probe_screener_yahoo_predefined,
    "screener.finviz": probe_screener_finviz,
    "screener.tradingview": probe_screener_tradingview,
    "llm.groq": probe_llm_groq,
    "llm.gemini": probe_llm_gemini,
    "llm.nvidia": probe_llm_nvidia,
}
