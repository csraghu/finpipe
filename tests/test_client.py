import pytest
from finpipe.client import Client


@pytest.mark.asyncio
async def test_client_initialization_and_close(config):
    async with Client(config) as client:
        assert client.catalog is not None
        assert client.health is not None
        assert client.catalog.capability("equity").provider("yahoo") is not None
        assert client.catalog.capability("options").provider("massive") is not None
        assert client.catalog.capability("screener").provider("tradingview") is not None

    assert client._registry.get("alpha_vantage")._client._client.is_closed
