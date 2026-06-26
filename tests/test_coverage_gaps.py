from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pandas as pd
import pytest
import respx
from finpipe.catalog.adapter_registry import AdapterRegistry
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeConfigError, FinpipeProviderDownError
from finpipe.core.models import OptionChain
from finpipe.health.registry import _is_provider_enabled, resolve_probe_keys
from finpipe.providers.composite import CompositeEquityService, call_with_fallback
from finpipe.providers.groq import GroqAdapter
from finpipe.providers.massive import MassiveOptionsAdapter
from finpipe.providers.sentiment import NewsSentimentAdapter
from finpipe.providers.yahoo import YahooFinanceAdapter


def test_is_provider_enabled_invalid_intel_and_screener_keys(config):
    assert _is_provider_enabled(config.providers, "intel.unknown") is False
    assert _is_provider_enabled(config.providers, "screener.unknown") is False
    assert _is_provider_enabled(config.providers, "unknown.provider") is False


def test_resolve_probe_keys_default_enabled_providers(config):
    keys = resolve_probe_keys(config)
    assert "equity.yahoo" in keys
    assert "options.massive" in keys


@pytest.mark.asyncio
async def test_adapter_registry_close_and_unknown_key(config):
    registry = AdapterRegistry(config)
    with pytest.raises(KeyError, match="Unknown adapter key"):
        registry.get("missing")
    assert registry.keys()
    await registry.close()


@pytest.mark.asyncio
async def test_call_with_fallback_raises_when_all_fail():
    primary = AsyncMock()
    primary.fail = AsyncMock(side_effect=RuntimeError("down"))
    with pytest.raises(RuntimeError):
        await call_with_fallback({"demo": primary}, ["demo"], "fail")


@pytest.mark.asyncio
async def test_composite_equity_options_chain_without_options_service(config):
    yahoo = AsyncMock()
    yahoo.get_options_chain = AsyncMock(
        return_value=OptionChain(symbol="AAPL", expiration_date=date.today())
    )
    equity = CompositeEquityService(config, adapters={"yahoo": yahoo, "alpha_vantage": AsyncMock()})
    chain = await equity.get_options_chain("AAPL")
    assert chain.symbol == "AAPL"


@pytest.mark.asyncio
async def test_groq_cache_hit_and_failures(config):
    adapter = GroqAdapter(config)
    cached = {
        "model_name": "llama3-8b-8192",
        "content": "cached",
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "raw_response": {},
    }
    adapter._cache.set(f"groq_llama3-8b-8192_{hash('prompt')}", cached, 60)
    resp = await adapter.generate_response("prompt")
    assert resp.content == "cached"

    with respx.mock:
        respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(FinpipeProviderDownError):
            await adapter.generate_response("fresh prompt")

    with respx.mock:
        respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": []})
        )
        with pytest.raises(FinpipeProviderDownError, match="empty response"):
            await adapter.generate_response("empty")


@pytest.mark.asyncio
async def test_groq_remote_models_without_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = FinpipeConfig.from_dict({"providers": {"groq": {"enabled": False}}})
    adapter = GroqAdapter(cfg)
    adapter._api_key = None
    assert await adapter._remote_models() == []


@pytest.mark.asyncio
async def test_massive_list_s3_files_failure(config):
    adapter = MassiveOptionsAdapter(config)
    s3 = AsyncMock()
    s3.list_objects_v2 = AsyncMock(side_effect=OSError("network"))
    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=s3)
    client_cm.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.client = MagicMock(return_value=client_cm)
    with patch.object(adapter, "_get_aioboto3_session", return_value=session):
        assert await adapter.list_s3_files("prefix/") == []


@pytest.mark.asyncio
async def test_massive_sync_flatfile_generic_error(config, tmp_path):
    adapter = MassiveOptionsAdapter(config)
    s3 = AsyncMock()
    s3.get_object = AsyncMock(side_effect=TimeoutError("slow"))
    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=s3)
    client_cm.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.client = MagicMock(return_value=client_cm)
    with patch.object(adapter, "_get_aioboto3_session", return_value=session):
        assert await adapter.sync_flatfile_from_s3("k", str(tmp_path / "f.csv")) is False


@pytest.mark.asyncio
async def test_yahoo_options_chain_parses_rows(config, mocker):
    adapter = YahooFinanceAdapter(config)
    mock_ticker = mocker.MagicMock()
    mock_ticker.options = ("2026-01-15",)
    calls = pd.DataFrame(
        {
            "contractSymbol": ["C1"],
            "strike": [100.0],
            "lastPrice": [1.0],
            "bid": [0.9],
            "ask": [1.1],
            "volume": [10],
            "openInterest": [100],
            "impliedVolatility": [0.2],
            "inTheMoney": [True],
        }
    )
    puts = pd.DataFrame(
        {
            "contractSymbol": ["P1"],
            "strike": [100.0],
            "lastPrice": [1.0],
            "bid": [0.9],
            "ask": [1.1],
            "volume": [10],
            "openInterest": [100],
            "impliedVolatility": [0.2],
            "inTheMoney": [False],
        }
    )
    chain = mocker.MagicMock()
    chain.calls = calls
    chain.puts = puts
    mock_ticker.option_chain.return_value = chain
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)

    result = await adapter.get_options_chain("AAPL", date(2026, 1, 15))
    assert len(result.calls) == 1
    assert len(result.puts) == 1


@pytest.mark.asyncio
async def test_sentiment_get_news_without_fetchers():
    cfg = FinpipeConfig.from_dict(
        {
            "providers": {
                "sentiment": {
                    "sources": {
                        "google_news": {"enabled": False},
                        "stocktwits": {"enabled": False},
                        "reddit": {"enabled": False},
                    }
                }
            }
        }
    )
    adapter = NewsSentimentAdapter(cfg)
    assert await adapter.get_news("AAPL") == []


def test_enabled_providers_require_keys_when_enabled(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cfg = FinpipeConfig.from_dict({"providers": {"groq": {"enabled": True}}})
    with pytest.raises(FinpipeConfigError):
        cfg.providers.groq.ensure_configured()

    monkeypatch.delenv("FRED_API_KEY", raising=False)
    cfg = FinpipeConfig.from_dict({"providers": {"fred": {"enabled": True}}})
    with pytest.raises(FinpipeConfigError):
        cfg.providers.fred.ensure_configured()

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = FinpipeConfig.from_dict({"providers": {"gemini": {"enabled": True}}})
    with pytest.raises(FinpipeConfigError):
        cfg.providers.gemini.ensure_configured()

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    cfg = FinpipeConfig.from_dict({"providers": {"nvidia": {"enabled": True}}})
    with pytest.raises(FinpipeConfigError):
        cfg.providers.nvidia.ensure_configured()

    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    cfg = FinpipeConfig.from_dict({"providers": {"alpha_vantage": {"enabled": True}}})
    with pytest.raises(FinpipeConfigError):
        cfg.providers.alpha_vantage.ensure_configured()
