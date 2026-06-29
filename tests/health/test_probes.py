from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeConfigError
from finpipe.core.models import LLMResponse, SocialPostKind, TickerMetadata
from finpipe.health import probes


def _client_with_config(config: FinpipeConfig | None = None) -> MagicMock:
    client = MagicMock()
    client.config = config or FinpipeConfig()
    return client


def _catalog_chain(client: MagicMock, capability: str, provider: str | None = None) -> Any:
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
    av.get_live_spot_price = AsyncMock(return_value=450.0)
    assert await probes.probe_equity_alpha_vantage(client, "SPY") is None


@pytest.mark.asyncio
async def test_probe_equity_alpha_vantage_degraded():
    client = _client_with_config()
    _, av = _catalog_chain(client, "equity", "alpha_vantage")
    av.get_live_spot_price = AsyncMock(return_value=None)
    assert (
        await probes.probe_equity_alpha_vantage(client, "SPY") == "spot price unavailable for SPY"
    )


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
    massive.fetch_options_snapshot = AsyncMock(return_value=[])
    assert await probes.probe_options_massive(client, "SPY") == "options snapshot empty"


@pytest.mark.asyncio
async def test_probe_options_massive_connected():
    client = _client_with_config()
    _, massive = _catalog_chain(client, "options", "massive")
    massive.fetch_options_snapshot = AsyncMock(return_value=[{"symbol": "O:SPY"}])
    assert await probes.probe_options_massive(client, "SPY") is None


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
    assert result == "no reddit posts returned for TSLA"
    intel.get_social_posts.assert_awaited_once_with("TSLA", limit=1, kind=SocialPostKind.FORUM)


@pytest.mark.asyncio
async def test_probe_intel_reddit_uses_configured_symbol():
    from finpipe.core.config import FinpipeConfig

    config = FinpipeConfig.from_dict({"health": {"reddit_probe_symbol": "NVDA"}})
    client = _client_with_config(config)
    intel = _catalog_chain(client, "intel")
    intel.get_social_posts = AsyncMock(return_value=[MagicMock()])
    assert await probes.probe_intel_reddit(client, "SPY") is None
    intel.get_social_posts.assert_awaited_once_with("NVDA", limit=1, kind=SocialPostKind.FORUM)


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
        "finviz screener returned no tickers (tried: geo_usa, ta_topgainers)"
    )
    assert screener.get_fundamental.await_count == 2


@pytest.mark.asyncio
async def test_probe_screener_finviz_fallback_filter():
    client = _client_with_config()
    screener = _catalog_chain(client, "screener")
    screener.get_fundamental = AsyncMock(side_effect=[[], ["AAPL"]])
    assert await probes.probe_screener_finviz(client, "SPY") is None
    assert screener.get_fundamental.await_count == 2


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
    groq.generate_response = AsyncMock(return_value=LLMResponse(model_name="x", content=""))
    assert await probes.probe_llm_groq(client, "SPY") == "groq returned empty completion"


@pytest.mark.asyncio
async def test_probe_llm_groq_connected():
    client = _client_with_config()
    _, groq = _catalog_chain(client, "llm", "groq")
    groq.generate_response = AsyncMock(return_value=LLMResponse(model_name="x", content="OK"))
    assert await probes.probe_llm_groq(client, "SPY") is None
    groq.generate_response.assert_awaited_once()
    call_kwargs = groq.generate_response.await_args.kwargs  # type: ignore
    assert call_kwargs["max_tokens"] == client.config.health.llm_probe_max_tokens


@pytest.mark.asyncio
async def test_probe_llm_gemini_connected():
    client = _client_with_config()
    _, gemini = _catalog_chain(client, "llm", "gemini")
    gemini.generate_response = AsyncMock(return_value=LLMResponse(model_name="x", content="OK"))
    assert await probes.probe_llm_gemini(client, "SPY") is None
    call_kwargs = gemini.generate_response.await_args.kwargs  # type: ignore
    max_out = call_kwargs["generationConfig"]["maxOutputTokens"]
    assert max_out == client.config.health.llm_probe_max_tokens


@pytest.mark.asyncio
async def test_probe_llm_nvidia_degraded():
    client = _client_with_config()
    _, nvidia = _catalog_chain(client, "llm", "nvidia")
    nvidia.generate_response = AsyncMock(return_value=LLMResponse(model_name="x", content=""))
    assert await probes.probe_llm_nvidia(client, "SPY") == "nvidia returned empty completion"


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


@pytest.mark.asyncio
@patch("finpipe.health.probes.compress_llm_text_for_sentiment")
async def test_probe_compression_huggingface_success(mock_compress):
    mock_compress.return_value = "compressed"
    client = _client_with_config()
    client.config = client.config.model_copy(
        update={"llm_prompt": client.config.llm_prompt.model_copy(
            update={"compression": client.config.llm_prompt.compression.model_copy(
                update={"endpoint_url": "http://test"}
            )}
        )}
    )
    assert await probes.probe_compression_huggingface(client, "SPY") is None
    mock_compress.assert_called_once()


@pytest.mark.asyncio
async def test_probe_compression_huggingface_missing_endpoint():
    client = _client_with_config()
    client.config = client.config.model_copy(
        update={"llm_prompt": client.config.llm_prompt.model_copy(
            update={"compression": client.config.llm_prompt.compression.model_copy(
                update={"endpoint_url": None}
            )}
        )}
    )
    assert "not configured" in await probes.probe_compression_huggingface(client, "SPY")  # type: ignore


@pytest.mark.asyncio
@patch("finpipe.health.probes.compress_llm_text_for_sentiment")
async def test_probe_compression_huggingface_empty(mock_compress):
    mock_compress.return_value = ""
    client = _client_with_config()
    client.config = client.config.model_copy(
        update={"llm_prompt": client.config.llm_prompt.model_copy(
            update={"compression": client.config.llm_prompt.compression.model_copy(
                update={"endpoint_url": "http://test"}
            )}
        )}
    )
    assert "returned empty" in await probes.probe_compression_huggingface(client, "SPY")  # type: ignore


@pytest.mark.asyncio
@patch("finpipe.health.probes.compress_llm_text_for_sentiment")
async def test_probe_compression_huggingface_exception(mock_compress):
    mock_compress.side_effect = Exception("error")
    client = _client_with_config()
    client.config = client.config.model_copy(
        update={"llm_prompt": client.config.llm_prompt.model_copy(
            update={"compression": client.config.llm_prompt.compression.model_copy(
                update={"endpoint_url": "http://test"}
            )}
        )}
    )
    assert "failed: error" in await probes.probe_compression_huggingface(client, "SPY")  # type: ignore


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
        "compression.huggingface",
    }
