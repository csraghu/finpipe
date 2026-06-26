import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Protocol

from finpipe.core.config import CacheConfig

logger = logging.getLogger(__name__)


class ICacheBackend(Protocol):
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_seconds: int | float) -> None: ...

    def verify_thread_safe(self) -> bool: ...

    def close(self) -> None: ...


class InMemoryTTLCache:
    def __init__(self, maxsize: int = 10000):
        from cachetools import LRUCache

        self._cache: LRUCache[str, tuple[Any, float]] = LRUCache(maxsize=maxsize)

    def get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._cache[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int | float) -> None:
        expiry = time.monotonic() + ttl_seconds
        self._cache[key] = (value, expiry)

    def verify_thread_safe(self) -> bool:
        return True

    def close(self) -> None:
        return None


class SqliteCacheBackend:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._ensure_schema()

    def _open_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path, timeout=60.0, isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=60000;")
            conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            pass
        return conn

    def _connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                self._conn = self._open_connection()
            return self._conn

    def _ensure_schema(self) -> None:
        conn = self._connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS finpipe_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                expiry_timestamp REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_expiry ON finpipe_cache(expiry_timestamp)")

    def get(self, key: str) -> Any | None:
        with self._lock:
            try:
                conn = self._connection()
                row = conn.execute(
                    "SELECT value, expiry_timestamp FROM finpipe_cache WHERE key = ?", (key,)
                ).fetchone()
                if row is None:
                    return None
                if time.time() > row["expiry_timestamp"]:
                    conn.execute("DELETE FROM finpipe_cache WHERE key = ?", (key,))
                    return None
                return json.loads(row["value"])
            except Exception as exc:
                logger.warning("Cache GET failed", extra={"key": key, "error": str(exc)})
                return None

    def set(self, key: str, value: Any, ttl_seconds: int | float) -> None:
        with self._lock:
            try:
                conn = self._connection()
                expiry = time.time() + ttl_seconds
                conn.execute(
                    """
                    INSERT INTO finpipe_cache (key, value, expiry_timestamp)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        expiry_timestamp=excluded.expiry_timestamp
                    """,
                    (key, json.dumps(value), expiry),
                )
            except Exception as exc:
                logger.warning("Cache SET failed", extra={"key": key, "error": str(exc)})

    def verify_thread_safe(self) -> bool:
        errors: list[BaseException] = []

        def writer() -> None:
            try:
                for i in range(20):
                    self.set(f"probe-{i}", {"i": i}, 60)
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return not errors

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


def create_cache_backend(config: CacheConfig) -> ICacheBackend:
    if config.cache_type == "sqlite":
        db_path = config.sqlite_path or config.sqlite_db_path
        return SqliteCacheBackend(db_path=db_path)
    if config.cache_type == "none":
        return _NoOpCache()
    return InMemoryTTLCache(maxsize=config.maxsize)


class _NoOpCache:
    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl_seconds: int | float) -> None:
        return None

    def verify_thread_safe(self) -> bool:
        return True

    def close(self) -> None:
        return None
