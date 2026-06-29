from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock

import httpx
import pandas as pd
import pytest
import respx
from finpipe.core.exceptions import FinpipeDataNotFoundError, FinpipeProviderDownError
from finpipe.core.models import SocialPostKind
from finpipe.core.registry import BuildContext
from finpipe.health import probes
from finpipe.providers.alpha_vantage import AlphaVantageAdapter
from finpipe.providers.massive import MassiveOptionsAdapter
from finpipe.providers.screener import ScreenerAdapter, build_screener
from finpipe.providers.sentiment import NewsSentimentAdapter, build_sentiment


@pytest.mark.asyncio
async def test_massive_get_options_chain_http_path(config):
    adapter = MassiveOptionsAdapter(config)
    payload = {
        "data": {
            "expiration_date": "2026-01-15",
            "calls": [{"contract_symbol": "C1", "strike": 100.0, "in_the_money": False}],
            "puts": [],
        }
    }
    with respx.mock:
        respx.get("https://api.massive.com/v1/options/chain").mock(
            return_value=httpx.Response(200, json=payload)
        )
        chain = await adapter.get_options_chain("AAPL", date(2026, 1, 15))
        assert chain.symbol == "AAPL"
        assert len(chain.calls) == 1


@pytest.mark.asyncio
async def test_massive_get_options_chain_failure(config):
    adapter = MassiveOptionsAdapter(config)
    with respx.mock:
        respx.get("https://api.massive.com/v1/options/chain").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(FinpipeDataNotFoundError):
            await adapter.get_options_chain("AAPL")


@pytest.mark.asyncio
async def test_massive_get_options_snapshot_cache_hit(config, pandas_config):
    adapter = MassiveOptionsAdapter(pandas_config)
    adapter._cache.set(
        f"massive_snap_AAPL_{hash(frozenset())}",
        {"contract_symbol": ["C1"]},
        60,
    )
    # Force cache key match by calling with no filters
    with respx.mock:
        respx.get("https://api.massive.com/v1/options/snapshot").mock(
            return_value=httpx.Response(200, json={"data": [{"contract_symbol": "C1"}]})
        )
        first = await adapter.get_options_snapshot("AAPL")
        second = await adapter.get_options_snapshot("AAPL")
        assert len(first) == len(second)


@pytest.mark.asyncio
async def test_screener_run_dispatch_and_failures(config):
    adapter = ScreenerAdapter(config)

    with respx.mock:
        respx.get("https://query1.finance.yahoo.com/v1/finance/trending/US").mock(
            return_value=httpx.Response(
                200, json={"finance": {"result": [{"quotes": [{"symbol": "AAPL"}]}]}}
            )
        )
        assert await adapter.run("yahoo_trending") == ["AAPL"]

        respx.get(url__startswith="https://query1.finance.yahoo.com/v1/finance/screener").mock(
            return_value=httpx.Response(
                200, json={"finance": {"result": [{"quotes": [{"symbol": "NVDA"}]}]}}
            )
        )
        assert await adapter.run("yahoo_predefined", scr_id="day_gainers") == ["NVDA"]

        respx.get(url__startswith="https://finviz.com/screener.ashx").mock(
            return_value=httpx.Response(200, text='<a href="stock?t=AMD">AMD</a>')
        )
        assert await adapter.run("finviz", filter_key="ta_topgainers") == ["AMD"]

        respx.post("https://scanner.tradingview.com/america/scan").mock(
            return_value=httpx.Response(200, json={"data": [{"d": ["NASDAQ:MSFT"]}]})
        )
        assert await adapter.run("tradingview", criteria={"limit": 1}) == ["MSFT"]

    await adapter.close()


@pytest.mark.asyncio
async def test_screener_tradingview_failure_raises(config):
    adapter = ScreenerAdapter(config)
    with respx.mock:
        respx.post("https://scanner.tradingview.com/america/scan").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(FinpipeProviderDownError):
            await adapter.run_tradingview({"limit": 1})
    await adapter.close()


@pytest.mark.asyncio
async def test_screener_finviz_failure_returns_empty(config):
    adapter = ScreenerAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://finviz.com/screener.ashx").mock(
            side_effect=httpx.ConnectError("down")
        )
        assert await adapter.get_fundamental("ta_topgainers") == []
    await adapter.close()


def test_build_screener_and_sentiment_factories(config):
    assert build_screener(BuildContext(config=config))
    assert build_sentiment(BuildContext(config=config))


@pytest.mark.asyncio
async def test_sentiment_cached_social_and_score(config):
    adapter = NewsSentimentAdapter(config)
    cache_kind = str(SocialPostKind.MICROBLOG)
    adapter._cache.set(
        f"social_AAPL_5_{cache_kind}",
        [
            {
                "kind": "microblog",
                "text": "hi",
                "url": "u",
                "author": "a",
                "created_at": None,
            }
        ],
        60,
    )
    posts = await adapter.get_social_posts("AAPL", limit=5, kind=SocialPostKind.MICROBLOG)
    assert posts[0].text == "hi"

    adapter._cache.set(
        "sentiment_AAPL",
        {
            "symbol": "AAPL",
            "source": "microblog",
            "timestamp": datetime.now().isoformat(),
            "score": 0.1,
            "magnitude": 1.0,
        },
        60,
    )
    score = await adapter.get_sentiment_score("AAPL")
    assert score.symbol == "AAPL"


@pytest.mark.asyncio
async def test_alpha_vantage_describe_and_close(config):
    adapter = AlphaVantageAdapter(config)
    info = await adapter.describe()
    assert info["provider_id"] == "alpha_vantage"
    await adapter.close()


@pytest.mark.asyncio
async def test_probe_success_paths(config):  # noqa: C901
    from finpipe.client import Client
    from finpipe.core.config import AlphaVantageConfig, FredConfig, MassiveConfig
    from finpipe.core.models import TickerMetadata
    av_cfg = AlphaVantageConfig(api_key="test", enabled=True)
    mas_cfg = MassiveConfig(api_key="test", enabled=True)
    fred_cfg = FredConfig(api_key="test", enabled=True)
    new_providers = config.providers.model_copy(update={
        "alpha_vantage": av_cfg,
        "massive": mas_cfg,
        "fred": fred_cfg
    })
    config = config.model_copy(update={"providers": new_providers})

    async with Client(config) as client:
        for key in probes.PROBE_RUNNERS:
            if key == "equity.yahoo":
                client._registry.get("yahoo").get_metadata = AsyncMock(
                    return_value=TickerMetadata(symbol="SPY")
                )
            if key == "equity.alpha_vantage":
                client._registry.get("alpha_vantage").get_live_spot_price = AsyncMock(
                    return_value=150.0
                )
            if key == "options.massive":
                client._registry.get("massive").fetch_options_snapshot = AsyncMock(
                    return_value=[object()]
                )
            if key == "options.yahoo":
                client._registry.get("yahoo").get_options_snapshot = AsyncMock(
                    return_value=pd.DataFrame({"x": [1]})
                )
            if key == "macro.fred":
                client._registry.get("fred").get_macro_series = AsyncMock(
                    return_value=pd.DataFrame({"v": [1]})
                )
            if key in ("intel.google_news",):
                client._registry.get("sentiment").get_news = AsyncMock(return_value=[object()])
            if key in ("intel.stocktwits", "intel.reddit"):
                client._registry.get("sentiment").get_social_posts = AsyncMock(
                    return_value=[object()]
                )
            if key == "screener.yahoo_trending":
                client._registry.get("screener").get_trending = AsyncMock(return_value=["AAPL"])
            if key == "screener.yahoo_predefined":
                client._registry.get("screener").get_predefined = AsyncMock(return_value=["AAPL"])
            if key == "screener.finviz":
                client._registry.get("screener").get_fundamental = AsyncMock(return_value=["AAPL"])
            if key == "screener.tradingview":
                client._registry.get("screener").run_tradingview = AsyncMock(return_value=["AAPL"])
            if key.startswith("llm."):
                provider = key.split(".", 1)[1]
                client._registry.get(provider).describe = AsyncMock(
                    return_value={"details": {"models": ["m"]}}
                )

        from unittest.mock import patch

        from finpipe.core.models import LLMResponse
        from finpipe.providers.gemini import GeminiAdapter
        from finpipe.providers.groq import GroqAdapter
        from finpipe.providers.nvidia import NvidiaAdapter

        with patch.object(GeminiAdapter, "generate_response", AsyncMock(return_value=LLMResponse(content="test", model_name="test"))), \
             patch.object(GroqAdapter, "generate_response", AsyncMock(return_value=LLMResponse(content="test", model_name="test"))), \
             patch.object(NvidiaAdapter, "generate_response", AsyncMock(return_value=LLMResponse(content="test", model_name="test"))):
            for key in probes.PROBE_RUNNERS:
                assert await probes.PROBE_RUNNERS[key](client, "SPY") is None
