from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeProviderDownError
from finpipe.core.models import (
    NewsArticle,
    OptionChain,
    SentimentScore,
    SocialPost,
    SocialPostKind,
)
from finpipe.providers.composite import (
    CompositeIntelService,
    CompositeMacroService,
    CompositeOptionsService,
    CompositeScreenerService,
    call_with_fallback,
    ordered_provider_names,
    resolve_first_adapter,
)


def test_ordered_provider_names_skips_duplicate_fallback():
    cfg = FinpipeConfig.from_dict(
        {"routing": {"equity_primary": "yahoo", "equity_fallback": "yahoo"}}
    )
    assert ordered_provider_names(
        cfg, primary_key="equity_primary", fallback_key="equity_fallback"
    ) == ["yahoo"]


@pytest.mark.asyncio
async def test_call_with_fallback_sync_method():
    adapter = MagicMock()
    adapter.get_value = MagicMock(return_value=42)

    result = await call_with_fallback({"demo": adapter}, ["demo"], "get_value")
    assert result == 42


@pytest.mark.asyncio
async def test_call_with_fallback_skips_missing_adapter_and_method():
    adapter = MagicMock(spec=[])
    with pytest.raises(FinpipeProviderDownError):
        await call_with_fallback({"demo": adapter}, ["demo", "missing"], "missing_method")


def test_resolve_first_adapter_none():
    assert resolve_first_adapter({}, ["missing"]) is None


@pytest.mark.asyncio
async def test_composite_options_delegates_all_methods(config):
    massive = AsyncMock()
    massive.api_key = "k"
    massive.fetch_options_contracts.return_value = []
    massive.fetch_options_snapshot.return_value = []
    massive.fetch_single_option_snapshot.return_value = {}
    massive.fetch_historical_aggs.return_value = []
    massive.sync_flatfile_from_s3.return_value = True
    massive.list_s3_files.return_value = []
    massive.get_options_chain = AsyncMock(
        return_value=OptionChain(symbol="AAPL", expiration_date=date.today())
    )
    massive.get_options_snapshot = AsyncMock(return_value=MagicMock())

    options = CompositeOptionsService(config, adapters={"massive": massive, "yahoo": AsyncMock()})
    assert options.api_key == "k"
    await options.fetch_options_contracts("AAPL")
    await options.fetch_options_snapshot("AAPL", expiration_date="2026-01-01")
    await options.fetch_single_option_snapshot("AAPL", "O:TEST")
    await options.fetch_historical_aggs("O:TEST", "2026-01-01", "2026-01-31")
    await options.sync_flatfile_from_s3("k", "/tmp/x")
    await options.list_s3_files("prefix")
    await options.get_options_chain("AAPL", date.today())
    await options.get_options_snapshot("AAPL", limit=5)


@pytest.mark.asyncio
async def test_composite_options_api_key_none_when_missing_adapter():
    cfg = FinpipeConfig()
    options = CompositeOptionsService(cfg, adapters={})
    assert options.api_key is None


@pytest.mark.asyncio
async def test_composite_macro_intel_screener_delegate(config):
    fred = AsyncMock()
    fred.get_macro_series.return_value = MagicMock()
    macro = CompositeMacroService(config, fred=fred)
    await macro.get_macro_series("DGS10", date.today(), date.today())

    sentiment = AsyncMock()
    sentiment.get_news.return_value = [
        NewsArticle(title="t", link="l", published_at=datetime.now())
    ]
    sentiment.get_social_posts.return_value = [
        SocialPost(kind=SocialPostKind.MICROBLOG, text="hi", url="u")
    ]
    sentiment.get_sentiment_score.return_value = SentimentScore(
        score=0.5, magnitude=1, source="combined", timestamp=datetime.now()
    )
    intel = CompositeIntelService(config, sentiment=sentiment)
    assert await intel.get_news("AAPL", limit=1)
    assert await intel.get_social_posts("AAPL", limit=1, kind=SocialPostKind.FORUM)
    assert (await intel.get_sentiment_score("AAPL")).score == 0.5

    screener = AsyncMock()
    screener.run.return_value = ["AAPL"]
    screener.get_trending.return_value = ["AAPL"]
    screener.get_predefined.return_value = ["AAPL"]
    screener.get_fundamental.return_value = ["AAPL"]
    screener.run_tradingview.return_value = ["AAPL"]
    composite_screener = CompositeScreenerService(config, screener=screener)
    await composite_screener.run("yahoo_trending")
    await composite_screener.get_trending()
    await composite_screener.get_predefined("day_gainers", limit=5)
    await composite_screener.get_fundamental("ta_topgainers")
    await composite_screener.run_tradingview({"limit": 1})
