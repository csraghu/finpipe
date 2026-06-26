from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeConfigError
from finpipe.core.models import SocialPostKind, TickerMetadata
from finpipe.health import probes


def _client_with_config(config: FinpipeConfig | None = None) -> MagicMock:
    client = MagicMock()
    client.config = config or FinpipeConfig()
    return client


def _catalog_chain(client: MagicMock, capability: str, provider: str | None = None):
    catalog = client.catalog
    capability_handle = MagicMock()
    catalog.capability = MagicMock(return_value=capability_handle)
    if provider is not None:
        provider_ref = MagicMock()
        capability_handle.provider = MagicMock(return_value=provider_ref)
        return capability_handle, provider_ref
    return capability_handle


@pytest.mark.asyncio
async def test_probe_equity_yahoo_connected():
    client = _client_with_config()
    _, yahoo = _catalog_chain(client, "equity", "yahoo")
    yahoo.get_metadata = AsyncMock(return_value=TickerMetadata(symbol="SPY"))
    assert await probes.probe_equity_yahoo(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_equity_yahoo_degraded():
    client = _client_with_config()
    _, yahoo = _catalog_chain(client, "equity", "yahoo")
    yahoo.get_metadata = AsyncMock(return_value=TickerMetadata(symbol=""))
    assert await probes.probe_equity_yahoo(client, "SPY") == "metadata missing symbol"


@pytest.mark.asyncio
async def test_probe_equity_alpha_vantage_missing_key(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    config = FinpipeConfig.from_dict({"providers": {"alpha_vantage": {"enabled": True}}})
    client = _client_with_config(config)
    with pytest.raises(FinpipeConfigError):
        await probes.probe_equity_alpha_vantage(client, "SPY")


@pytest.mark.asyncio
async def test_probe_equity_alpha_vantage_connected():
    client = _client_with_config()
    _, av = _catalog_chain(client, "equity", "alpha_vantage")
    av.get_metadata = AsyncMock(return_value=TickerMetadata(symbol="SPY"))
    assert await probes.probe_equity_alpha_vantage(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_options_massive_missing_key(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    config = FinpipeConfig.from_dict({"providers": {"massive": {"enabled": True}}})
    client = _client_with_config(config)
    with pytest.raises(FinpipeConfigError):
        await probes.probe_options_massive(client, "SPY")


@pytest.mark.asyncio
async def test_probe_options_massive_degraded():
    client = _client_with_config()
    _, massive = _catalog_chain(client, "options", "massive")
    massive.get_options_snapshot = AsyncMock(return_value=pl.DataFrame())
    assert await probes.probe_options_massive(client, "SPY") == "options snapshot empty"


@pytest.mark.asyncio
async def test_probe_options_yahoo_connected():
    client = _client_with_config()
    _, yahoo = _catalog_chain(client, "options", "yahoo")
    yahoo.get_options_snapshot = AsyncMock(return_value=pl.DataFrame({"x": [1]}))
    assert await probes.probe_options_yahoo(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_macro_fred_missing_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    config = FinpipeConfig.from_dict({"providers": {"fred": {"enabled": True}}})
    client = _client_with_config(config)
    with pytest.raises(FinpipeConfigError):
        await probes.probe_macro_fred(client, "SPY")


@pytest.mark.asyncio
async def test_probe_macro_fred_connected():
    client = _client_with_config()
    _, fred = _catalog_chain(client, "macro", "fred")
    fred.get_macro_series = AsyncMock(return_value=pl.DataFrame({"value": [1.0]}))
    assert await probes.probe_macro_fred(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_intel_google_news_degraded():
    client = _client_with_config()
    intel = _catalog_chain(client, "intel")
    intel.get_news = AsyncMock(return_value=[])
    assert await probes.probe_intel_google_news(client, "SPY") == "no news articles returned"


@pytest.mark.asyncio
async def test_probe_intel_stocktwits_connected():
    client = _client_with_config()
    intel = _catalog_chain(client, "intel")
    intel.get_social_posts = AsyncMock(return_value=[MagicMock()])
    assert await probes.probe_intel_stocktwits(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_intel_reddit_degraded():
    client = _client_with_config()
    intel = _catalog_chain(client, "intel")
    intel.get_social_posts = AsyncMock(return_value=[])
    result = await probes.probe_intel_reddit(client, "SPY")
    assert result == "no reddit posts returned"
    intel.get_social_posts.assert_awaited_once_with("SPY", limit=1, kind=SocialPostKind.FORUM)


@pytest.mark.asyncio
async def test_probe_screener_yahoo_trending_degraded():
    client = _client_with_config()
    screener = _catalog_chain(client, "screener")
    screener.get_trending = AsyncMock(return_value=[])
    assert await probes.probe_screener_yahoo_trending(client, "SPY") == (
        "trending screener returned no tickers"
    )


@pytest.mark.asyncio
async def test_probe_screener_yahoo_predefined_connected():
    client = _client_with_config()
    screener = _catalog_chain(client, "screener")
    screener.get_predefined = AsyncMock(return_value=["AAPL"])
    assert await probes.probe_screener_yahoo_predefined(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_screener_finviz_degraded():
    client = _client_with_config()
    screener = _catalog_chain(client, "screener")
    screener.get_fundamental = AsyncMock(return_value=[])
    assert await probes.probe_screener_finviz(client, "SPY") == (
        "finviz screener returned no tickers"
    )


@pytest.mark.asyncio
async def test_probe_screener_tradingview_connected():
    client = _client_with_config()
    screener = _catalog_chain(client, "screener")
    screener.run_tradingview = AsyncMock(return_value=["AAPL"])
    assert await probes.probe_screener_tradingview(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_llm_groq_missing_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    config = FinpipeConfig.from_dict({"providers": {"groq": {"enabled": True}}})
    client = _client_with_config(config)
    with pytest.raises(FinpipeConfigError):
        await probes.probe_llm_groq(client, "SPY")


@pytest.mark.asyncio
async def test_probe_llm_groq_degraded():
    client = _client_with_config()
    _, groq = _catalog_chain(client, "llm", "groq")
    groq.describe = AsyncMock(return_value={"details": {"models": []}})
    assert await probes.probe_llm_groq(client, "SPY") == "groq models list empty"


@pytest.mark.asyncio
async def test_probe_llm_gemini_connected():
    client = _client_with_config()
    _, gemini = _catalog_chain(client, "llm", "gemini")
    gemini.describe = AsyncMock(return_value={"details": {"models": ["gemini"]}})
    assert await probes.probe_llm_gemini(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_llm_nvidia_degraded(monkeypatch):
    client = _client_with_config()
    _, nvidia = _catalog_chain(client, "llm", "nvidia")
    nvidia.describe = AsyncMock(return_value={"details": {"models": []}})
    assert await probes.probe_llm_nvidia(client, "SPY") == "nvidia models list empty"


@pytest.mark.asyncio
async def test_probe_options_massive_connected():
    client = _client_with_config()
    _, massive = _catalog_chain(client, "options", "massive")
    massive.get_options_snapshot = AsyncMock(return_value=pl.DataFrame({"x": [1]}))
    assert await probes.probe_options_massive(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_options_yahoo_degraded():
    client = _client_with_config()
    _, yahoo = _catalog_chain(client, "options", "yahoo")
    yahoo.get_options_snapshot = AsyncMock(return_value=None)
    assert await probes.probe_options_yahoo(client, "SPY") == "options snapshot empty"


@pytest.mark.asyncio
async def test_probe_macro_fred_degraded():
    client = _client_with_config()
    _, fred = _catalog_chain(client, "macro", "fred")
    fred.get_macro_series = AsyncMock(return_value=pl.DataFrame())
    assert await probes.probe_macro_fred(client, "SPY") == "macro series empty"


@pytest.mark.asyncio
async def test_probe_intel_google_news_connected():
    client = _client_with_config()
    intel = _catalog_chain(client, "intel")
    intel.get_news = AsyncMock(return_value=[MagicMock()])
    assert await probes.probe_intel_google_news(client, "SPY") is None


def test_probe_runners_registry_keys():
    assert set(probes.PROBE_RUNNERS) == {
        "equity.yahoo",
        "equity.alpha_vantage",
        "options.massive",
        "options.yahoo",
        "macro.fred",
        "intel.google_news",
        "intel.stocktwits",
        "intel.reddit",
        "screener.yahoo_trending",
        "screener.yahoo_predefined",
        "screener.finviz",
        "screener.tradingview",
        "llm.groq",
        "llm.gemini",
        "llm.nvidia",
    }
