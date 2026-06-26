import httpx
import pytest
import respx
from finpipe.core.exceptions import FinpipeDataNotFoundError
from finpipe.providers.gemini import GeminiAdapter
from finpipe.providers.massive import MassiveOptionsAdapter
from finpipe.providers.nvidia import NvidiaAdapter


@pytest.mark.asyncio
async def test_nvidia_remote_models_without_api_key(config):
    adapter = NvidiaAdapter(config)
    adapter._api_key = None
    assert await adapter._remote_models() == []


@pytest.mark.asyncio
async def test_massive_options_snapshot_failure(config):
    adapter = MassiveOptionsAdapter(config)
    with respx.mock:
        respx.get("https://api.massive.com/v1/options/snapshot").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(FinpipeDataNotFoundError):
            await adapter.get_options_snapshot("AAPL")


@pytest.mark.asyncio
async def test_gemini_remote_models_without_api_key(config):
    adapter = GeminiAdapter(config)
    adapter._api_key = None
    assert await adapter._remote_models() == []
