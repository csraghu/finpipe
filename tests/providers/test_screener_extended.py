import httpx
import pytest
import respx
from finpipe.core.config import FinpipeConfig
from finpipe.providers.screener import ScreenerAdapter


@pytest.mark.asyncio
async def test_screener_describe(config):
    adapter = ScreenerAdapter(config)
    info = await adapter.describe()
    assert info["provider_id"] == "screener"
    await adapter.close()


@pytest.mark.asyncio
async def test_screener_disabled_provider_returns_empty():
    cfg = FinpipeConfig.from_dict({"providers": {"screener": {"enabled": False}}})
    adapter = ScreenerAdapter(cfg)
    assert await adapter.get_trending() == []
    await adapter.close()


@pytest.mark.asyncio
async def test_screener_trending_failure_returns_empty(config):
    adapter = ScreenerAdapter(config)
    with respx.mock:
        respx.get("https://query1.finance.yahoo.com/v1/finance/trending/US").mock(
            side_effect=httpx.ConnectError("down")
        )
        assert await adapter.get_trending() == []
    await adapter.close()


@pytest.mark.asyncio
async def test_screener_predefined_cache_hit(config):
    adapter = ScreenerAdapter(config)
    adapter._cache.set("screener_src_yahoo_predefined_day_gainers_50", ["AAPL"], 60)
    assert await adapter.get_predefined("day_gainers") == ["AAPL"]
    await adapter.close()


@pytest.mark.asyncio
async def test_screener_run_unknown_source(config):
    adapter = ScreenerAdapter(config)
    with pytest.raises(ValueError, match="Unknown screener source"):
        await adapter.run("not_a_source")
    await adapter.close()
