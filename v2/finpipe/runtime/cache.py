"""Async cache layer: protocol, memory/sqlite backends, namespaced views.

Fixes from the review:
- §2.2 strict codec — unserializable values raise (strict mode) instead of silent no-op
- namespacing baked into every key via ``NamespacedCache`` (v1 adapters ignored it)
- SQLite access via ``asyncio.to_thread`` — no blocking I/O on the event loop
- TTL semantics per the architecture doc: ``ttl <= 0`` means "always stale on read,
  still stored" so a stale-on-rate-limit path can use ``get_stale``.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from typing import Any, Protocol

from . import codec

logger = logging.getLogger(__name__)


class CacheBackend(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def get_stale(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl_s: float) -> None: ...
    async def close(self) -> None: ...


class MemoryCache:
    def __init__(self, maxsize: int = 1024, *, strict: bool = False) -> None:
        from cachetools import LRUCache

        self._entries: LRUCache[str, tuple[str, float]] = LRUCache(maxsize=maxsize)
        self._strict = strict
        self._lock = threading.Lock()

    async def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return None
        raw, expiry = entry
        if time.monotonic() > expiry:
            return None
        return codec.loads(raw)

    async def get_stale(self, key: str) -> Any | None:
        with self._lock:
            entry = self._entries.get(key)
        return codec.loads(entry[0]) if entry is not None else None

    async def set(self, key: str, value: Any, ttl_s: float) -> None:
        raw = _encode_or_report(key, value, strict=self._strict)
        if raw is None:
            return
        # ttl <= 0: stored but already expired for normal reads (get_stale still sees it)
        with self._lock:
            self._entries[key] = (raw, time.monotonic() + ttl_s)

    async def close(self) -> None:
        return None


class SqliteCache:
    """One connection, guarded by a lock; all calls hop to a worker thread."""

    def __init__(self, db_path: str, *, strict: bool = False) -> None:
        import os

        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._strict = strict
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, timeout=60.0, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=60000;")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS finpipe_cache_v2 (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL,
                   expires_at REAL NOT NULL,
                   stored_at REAL NOT NULL
               )"""
        )

    def _read(self, key: str, *, include_expired: bool) -> Any | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value, expires_at FROM finpipe_cache_v2 WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        raw, expires_at = row
        if not include_expired and time.time() > expires_at:
            return None
        return codec.loads(raw)

    def _write(self, key: str, raw: str, ttl_s: float) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO finpipe_cache_v2 (key, value, expires_at, stored_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       value=excluded.value, expires_at=excluded.expires_at, stored_at=excluded.stored_at""",
                (key, raw, now + ttl_s, now),
            )

    async def get(self, key: str) -> Any | None:
        return await asyncio.to_thread(self._read, key, include_expired=False)

    async def get_stale(self, key: str) -> Any | None:
        return await asyncio.to_thread(self._read, key, include_expired=True)

    async def set(self, key: str, value: Any, ttl_s: float) -> None:
        raw = _encode_or_report(key, value, strict=self._strict)
        if raw is None:
            return
        await asyncio.to_thread(self._write, key, raw, ttl_s)

    async def close(self) -> None:
        with self._lock:
            self._conn.close()


class NullCache:
    async def get(self, key: str) -> Any | None:
        return None

    async def get_stale(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, ttl_s: float) -> None:
        return None

    async def close(self) -> None:
        return None


def _encode_or_report(key: str, value: Any, *, strict: bool) -> str | None:
    try:
        return codec.dumps(value)
    except codec.CodecError:
        if strict:
            raise
        logger.error("Cache SET dropped for %s: value not canonically serializable", key)
        return None


class CacheManager:
    """Process-wide singleton registry: one backend per (type, path, namespace)."""

    _instances: dict[str, CacheBackend] = {}
    _lock = threading.RLock()

    @classmethod
    def get_shared(cls, cache_type: str, db_path: str, namespace: str, *, maxsize: int, strict: bool) -> CacheBackend:
        key = f"{cache_type}:{db_path}:{namespace}"
        with cls._lock:
            backend = cls._instances.get(key)
            if backend is None:
                backend = _create_backend(cache_type, db_path, maxsize=maxsize, strict=strict)
                cls._instances[key] = backend
            return backend

    @classmethod
    def shutdown(cls) -> None:
        with cls._lock:
            instances = list(cls._instances.values())
            cls._instances.clear()
        for backend in instances:
            # close() is async; backends here only close sqlite handles, do it inline
            conn_close = getattr(backend, "_conn", None)
            if conn_close is not None:
                try:
                    conn_close.close()
                except Exception:  # pragma: no cover - defensive
                    logger.debug("Ignored cache close error", exc_info=True)

    @classmethod
    def reset(cls) -> None:
        cls.shutdown()


def _create_backend(cache_type: str, db_path: str, *, maxsize: int, strict: bool) -> CacheBackend:
    if cache_type == "sqlite":
        return SqliteCache(db_path, strict=strict)
    if cache_type == "none":
        return NullCache()
    return MemoryCache(maxsize=maxsize, strict=strict)


def resolve_cache_backend(cache_config: Any) -> CacheBackend:
    """Build (or fetch the shared) backend from a ``CacheConfig``."""
    from .paths import default_cache_db_path

    db_path = cache_config.sqlite_path or default_cache_db_path()
    if cache_config.singleton:
        return CacheManager.get_shared(
            cache_config.cache_type, db_path, cache_config.namespace,
            maxsize=cache_config.maxsize, strict=cache_config.strict,
        )
    return _create_backend(
        cache_config.cache_type, db_path, maxsize=cache_config.maxsize, strict=cache_config.strict
    )


class NamespacedCache:
    """Adapter-facing view: every key is prefixed, adapters cannot escape their namespace."""

    def __init__(self, backend: CacheBackend, app_namespace: str, provider_key: str) -> None:
        self._backend = backend
        self._prefix = f"{app_namespace}:{provider_key}:"

    def key(self, endpoint: str, *parts: Any) -> str:
        return f"{self._prefix}{endpoint}:{codec.digest_key(*parts)}"

    async def get(self, key: str) -> Any | None:
        return await self._backend.get(key)

    async def get_stale(self, key: str) -> Any | None:
        return await self._backend.get_stale(key)

    async def set(self, key: str, value: Any, ttl_s: float) -> None:
        await self._backend.set(key, value, ttl_s)
