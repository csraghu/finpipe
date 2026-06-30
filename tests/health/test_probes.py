from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from finpipe.health.probes import universal_probe_runner
from finpipe.core.exceptions import (
    FinpipeConfigError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)
from finpipe.core.interfaces import (
    IHistoricalPriceProvider,
    IMetadataProvider,
    IOptionsProvider,
    IMacroProvider,
    IMarketIntelProvider,
    IScreenerProvider,
    ILLMProvider,
)
from finpipe.core.models import TickerMetadata


@pytest.fixture
def mock_client():
    client = MagicMock()
    client._registry = MagicMock()
    client.config = MagicMock()
    return client


@pytest.mark.asyncio
async def test_universal_probe_not_found(mock_client):
    mock_client._registry.get.side_effect = KeyError
    res = await universal_probe_runner(mock_client, "AAPL", "unknown")
    assert "not found" in res


@pytest.mark.asyncio
async def test_universal_probe_success_equity(mock_client):
    provider = MagicMock(spec=IHistoricalPriceProvider)
    provider.get_historical_prices = AsyncMock(return_value=[1, 2, 3])
    
    # Needs to not implement others
    mock_client._registry.get.return_value = provider
    
    res = await universal_probe_runner(mock_client, "AAPL", "dummy")
    assert res is None


@pytest.mark.asyncio
async def test_universal_probe_degraded_equity(mock_client):
    class DummyEquityProvider(IHistoricalPriceProvider, IMetadataProvider):
        async def get_historical_prices(self, symbol, start, end):
            raise ValueError("bad data")
            
        async def get_metadata(self, symbol):
            class Meta:
                symbol = None
            return Meta()
            
        async def get_financial_statements(self, symbol):
            raise NotImplementedError()

    provider = DummyEquityProvider()
    mock_client._registry.get.return_value = provider
    
    res = await universal_probe_runner(mock_client, "AAPL", "dummy")
    assert "get_historical_prices failed" in res
    assert "missing symbol" in res


@pytest.mark.asyncio
async def test_universal_probe_raises_finpipe_exceptions(mock_client):
    class DummyEquityProvider(IHistoricalPriceProvider):
        async def get_historical_prices(self, symbol, start, end):
            raise FinpipeProviderDownError("down")

    provider = DummyEquityProvider()
    mock_client._registry.get.return_value = provider
    
    with pytest.raises(FinpipeProviderDownError):
        await universal_probe_runner(mock_client, "AAPL", "dummy")


@pytest.mark.asyncio
async def test_universal_probe_success_options(mock_client):
    class DummyOptionsProvider(IOptionsProvider):
        async def get_options_chain(self, symbol):
            return {"chain": True}
            
        async def get_options_snapshot(self, symbol, limit=1):
            return [{"snap": True}]

    provider = DummyOptionsProvider()
    mock_client._registry.get.return_value = provider
    
    res = await universal_probe_runner(mock_client, "AAPL", "dummy")
    assert res is None


@pytest.mark.asyncio
async def test_universal_probe_degraded_options(mock_client):
    class DummyOptionsProvider(IOptionsProvider):
        async def get_options_chain(self, symbol):
            raise ValueError("chain error")
            
        async def get_options_snapshot(self, symbol, limit=1):
            class EmptyRet:
                def is_empty(self): return True
            return EmptyRet()

    provider = DummyOptionsProvider()
    mock_client._registry.get.return_value = provider
    
    res = await universal_probe_runner(mock_client, "AAPL", "dummy")
    assert "get_options_chain failed" in res
    assert "get_options_snapshot returned empty" in res


@pytest.mark.asyncio
async def test_universal_probe_success_macro(mock_client):
    class DummyMacroProvider(IMacroProvider):
        async def get_macro_series(self, series_id, start_date, end_date):
            return [1]

    provider = DummyMacroProvider()
    mock_client._registry.get.return_value = provider
    
    res = await universal_probe_runner(mock_client, "AAPL", "dummy")
    assert res is None


@pytest.mark.asyncio
async def test_universal_probe_success_intel(mock_client):
    class DummyIntelProvider(IMarketIntelProvider):
        async def get_news(self, symbol, limit=1):
            return [{"news": True}]
        async def get_social_posts(self, symbol, limit, kind=None):
            return [{"post": True}]
        async def get_sentiment_score(self, symbol):
            return {"score": 0.5}

    provider = DummyIntelProvider()
    mock_client._registry.get.return_value = provider
    
    res = await universal_probe_runner(mock_client, "AAPL", "dummy")
    assert res is None


@pytest.mark.asyncio
async def test_universal_probe_success_screener(mock_client):
    class DummyScreenerProvider(IScreenerProvider):
        async def run_screener(self, criteria):
            return ["AAPL"]

    provider = DummyScreenerProvider()
    mock_client._registry.get.return_value = provider
    
    res = await universal_probe_runner(mock_client, "AAPL", "dummy")
    assert res is None


@pytest.mark.asyncio
async def test_universal_probe_success_llm(mock_client):
    class DummyLLMProvider(ILLMProvider):
        async def generate_response(self, prompt, model=None, **kwargs):
            class Resp:
                content = "hello"
            return Resp()

    provider = DummyLLMProvider()
    mock_client.config.health.llm_probe_prompt = "test"
    mock_client.config.health.llm_probe_max_tokens = 10
    mock_client._registry.get.return_value = provider
    
    res = await universal_probe_runner(mock_client, "AAPL", "dummy")
    assert res is None
