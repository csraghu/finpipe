import pytest
from finpipe.client import Client


@pytest.mark.asyncio
async def test_client_initialization_and_close(config):
    async with Client(config) as client:
        assert client.yahoo is not None
        assert client.massive is not None
        assert client.tradingview is not None

    # After exiting the context manager, sessions should be closed
    assert client.alpha_vantage._client._client.is_closed
