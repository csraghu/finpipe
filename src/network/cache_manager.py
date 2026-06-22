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
                    raise FinpipeConfigError(
                        "SQLite cache failed concurrency self-test; "
                        "set cache.cache_type=memory for dev or fix permissions/locking"
                    )
                cls._instances[key] = backend
            return cls._instances[key]

    @classmethod
    def reset(cls) -> None:
        with cls._init_lock:
            cls._instances.clear()
