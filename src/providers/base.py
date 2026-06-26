from finpipe.core.config import FinpipeConfig
from finpipe.core.interfaces import ICloseable
from finpipe.network.cache import ICacheBackend
from finpipe.network.cache_manager import resolve_cache_backend


class ProviderBase(ICloseable):
    namespace: str = "base"
    config: FinpipeConfig

    def __init__(self, config: FinpipeConfig) -> None:
        self.config = config

    @property
    def cache(self) -> ICacheBackend:
        return resolve_cache_backend(self.config.cache)

    def cache_key(self, endpoint: str, *parts: str) -> str:
        prefix = f"{self.config.cache.namespace}:{self.namespace}:{endpoint}"
        if parts:
            return f"{prefix}:{':'.join(parts)}"
        return prefix

    async def close(self) -> None:
        return None
