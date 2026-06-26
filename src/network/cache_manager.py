from __future__ import annotations

import threading
from typing import ClassVar

from finpipe.core.config import CacheConfig
from finpipe.core.exceptions import FinpipeConfigError
from finpipe.network.cache import ICacheBackend, create_cache_backend


class CacheManager:
    """Process-wide singleton registry for shared cache backends."""

    _instances: ClassVar[dict[str, ICacheBackend]] = {}
    _init_lock: ClassVar[threading.RLock] = threading.RLock()

    @classmethod
    def _identity(cls, config: CacheConfig) -> str:
        db_path = config.sqlite_path or config.sqlite_db_path
        return f"{config.cache_type}:{db_path}:{config.namespace}"

    @classmethod
    def get_shared(cls, config: CacheConfig) -> ICacheBackend:
        if not config.singleton:
            return create_cache_backend(config)
        key = cls._identity(config)
        with cls._init_lock:
            if key not in cls._instances:
                backend = create_cache_backend(config)
                if config.cache_type == "sqlite" and not backend.verify_thread_safe():
                    close = getattr(backend, "close", None)
                    if callable(close):
                        close()
                    raise FinpipeConfigError(
                        "SQLite cache failed concurrency self-test; "
                        "set cache.cache_type=memory for dev or fix permissions/locking"
                    )
                cls._instances[key] = backend
            return cls._instances[key]

    @classmethod
    def shutdown(cls) -> None:
        """Close and drop all singleton cache backends."""
        with cls._init_lock:
            for backend in cls._instances.values():
                backend.close()
            cls._instances.clear()

    @classmethod
    def reset(cls) -> None:
        cls.shutdown()


def resolve_cache_backend(config: CacheConfig) -> ICacheBackend:
    """Return a shared or dedicated cache backend based on config."""
    if config.singleton:
        return CacheManager.get_shared(config)
    return create_cache_backend(config)
