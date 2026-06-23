import httpx
import pytest
import respx
from finpipe.core.config import FinpipeConfig
from finpipe.providers.screener import ScreenerAdapter


def _yahoo_quotes_payload(*symbols: str) -> dict:
    return {"finance": {"result": [{"quotes": [{"symbol": s} for s in symbols]}]}}


@pytest.mark.asyncio
async def test_screener_get_trending(config):
    adapter = ScreenerAdapter(config)
    payload = _yahoo_quotes_payload("aapl", "BTC-USD", "MSFT")

    with respx.mock:
        respx.get("https://query1.finance.yahoo.com/v1/finance/trending/US").mock(
            return_value=httpx.Response(200, json=payload)
        )
        symbols = await adapter.get_trending()
        assert symbols == ["AAPL", "MSFT"]

    await adapter.close()


@pytest.mark.asyncio
async def test_screener_get_predefined(config):
    adapter = ScreenerAdapter(config)

    with respx.mock:
        route = respx.get(
            url__startswith=(
                "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
            )
        ).mock(return_value=httpx.Response(200, json=_yahoo_quotes_payload("NVDA", "AMD")))
        symbols = await adapter.get_predefined("day_gainers")
        assert symbols == ["AMD", "NVDA"]
        symbols_again = await adapter.get_predefined("day_gainers")
        assert symbols_again == ["AMD", "NVDA"]
        assert route.call_count == 1

    await adapter.close()


@pytest.mark.asyncio
async def test_screener_get_fundamental(config):
    adapter = ScreenerAdapter(config)
    html = '<a href="stock?t=NVDA&ty=c&p=d&b=1">NVDA</a>'

    with respx.mock:
        respx.get(url__startswith="https://finviz.com/screener.ashx").mock(
            return_value=httpx.Response(200, text=html)
        )
        symbols = await adapter.get_fundamental("ta_topgainers")
        assert symbols == ["NVDA"]

    await adapter.close()


@pytest.mark.asyncio
async def test_screener_run_tradingview(config):
    adapter = ScreenerAdapter(config)
    json_mock = {"data": [{"d": ["NASDAQ:AAPL"]}, {"d": ["NYSE:MSFT"]}]}

    with respx.mock:
        respx.post("https://scanner.tradingview.com/america/scan").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        symbols = await adapter.run_tradingview({"limit": 2})
        assert symbols == ["AAPL", "MSFT"]

    await adapter.close()


@pytest.mark.asyncio
async def test_screener_run_dispatch(config):
    adapter = ScreenerAdapter(config)

    with respx.mock:
        respx.get("https://query1.finance.yahoo.com/v1/finance/trending/US").mock(
            return_value=httpx.Response(200, json=_yahoo_quotes_payload("TSLA"))
        )
        symbols = await adapter.run("yahoo_trending")
        assert symbols == ["TSLA"]

    await adapter.close()


@pytest.mark.asyncio
async def test_screener_source_disabled(config):
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "screener": {
                    "sources": {
                        "yahoo_trending": {"enabled": False},
                    }
                }
            }
        }
    )
    adapter = ScreenerAdapter(config)
    assert await adapter.get_trending() == []
    await adapter.close()


@pytest.mark.asyncio
async def test_screener_tradingview_legacy_ttl_merge():
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "tradingview": {"ttls": {"screener_sec": 600}},
                "screener": {
                    "sources": {
                        "tradingview": {"ttls": {"fetch_sec": None}},
                    }
                },
            }
        }
    )
    assert config.providers.screener.resolve_source_fetch_ttl(
        "tradingview",
        legacy_tradingview=config.providers.tradingview.ttls,
    ) == 600
