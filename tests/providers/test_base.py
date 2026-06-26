from finpipe.core.config import FinpipeConfig
from finpipe.network.cache_manager import CacheManager
from finpipe.providers.base import ProviderBase


class _StubProvider(ProviderBase):
    namespace = "stub"


def test_provider_base_cache_and_keys(config):
    CacheManager.reset()
    provider = _StubProvider(config)
    assert provider.cache_key("quotes", "AAPL") == "default:stub:quotes:AAPL"
    assert provider.cache_key("quotes") == "default:stub:quotes"
    provider.cache.set("default:stub:quotes:AAPL", 1, 60)
    assert provider.cache.get("default:stub:quotes:AAPL") == 1


def test_provider_base_singleton_cache(config):
    CacheManager.reset()
    singleton_config = FinpipeConfig.from_dict(
        {"cache": {"cache_type": "memory", "singleton": True, "namespace": "shared"}}
    )
    first = _StubProvider(singleton_config)
    second = _StubProvider(singleton_config)
    first.cache.set("default:stub:quotes:MSFT", 42, 60)
    assert second.cache.get("default:stub:quotes:MSFT") == 42


async def test_provider_base_close_is_noop(config):
    provider = _StubProvider(config)
    await provider.close()
