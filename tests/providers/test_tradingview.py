import httpx
import pytest
import respx

from finpipe.providers.tradingview import TradingViewAdapter


@pytest.mark.asyncio
async def test_tradingview_screener(config):
    adapter = TradingViewAdapter(config)

    json_mock = {"data": [{"d": ["NASDAQ:AAPL"]}, {"d": ["NYSE:MSFT"]}]}

    with respx.mock:
        respx.post("https://scanner.tradingview.com/america/scan").mock(
            return_value=httpx.Response(200, json=json_mock)
        )

        symbols = await adapter.run_screener({"limit": 2})
        assert symbols == ["AAPL", "MSFT"]
