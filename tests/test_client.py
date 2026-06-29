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

    resilient = client._registry.get("alpha_vantage")._client
    assert resilient._httpx_client is None and resilient._curl_session is None


@pytest.mark.asyncio
async def test_client_close_shuts_down_singleton_cache(tmp_path):
    from finpipe.core.config import FinpipeConfig
    from finpipe.network.cache_manager import CacheManager

    cfg = FinpipeConfig.from_dict(
        {
            "cache": {
                "cache_type": "sqlite",
                "sqlite_path": str(tmp_path / "cache.db"),
                "singleton": True,
            }
        }
    )
    CacheManager.reset()
    async with Client(cfg):
        pass
    assert not CacheManager._instances


async def test_client_dump_settings(config):
    async with Client(config) as client:
        dumped = client.dump_settings(redact_secrets=True)
        assert "providers" in dumped
        json_payload = client.dump_settings_json(redact_secrets=True)
        assert "providers" in json_payload
