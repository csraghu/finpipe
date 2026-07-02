"""Cache layer contract tests: strict mode, TTL semantics, namespacing, sqlite parity."""

from __future__ import annotations

from datetime import datetime

import pytest
from finpipe.runtime.cache import MemoryCache, NamespacedCache, SqliteCache
from finpipe.runtime.codec import CodecError


async def test_memory_set_get_and_ttl_zero_is_stale_but_stored():
    cache = MemoryCache(strict=True)
    await cache.set("k", {"v": 1}, ttl_s=60)
    assert await cache.get("k") == {"v": 1}

    await cache.set("stale", {"v": 2}, ttl_s=0)
    assert await cache.get("stale") is None          # ttl=0 → never fresh
    assert await cache.get_stale("stale") == {"v": 2}  # …but stored for degradation


async def test_strict_mode_raises_on_unserializable():
    cache = MemoryCache(strict=True)

    class Opaque: ...

    with pytest.raises(CodecError):
        await cache.set("bad", Opaque(), ttl_s=60)


async def test_non_strict_drops_without_raising():
    cache = MemoryCache(strict=False)

    class Opaque: ...

    await cache.set("bad", Opaque(), ttl_s=60)  # logged, not raised
    assert await cache.get("bad") is None


async def test_sqlite_round_trips_datetime_payloads(tmp_path):
    """v1 regression: datetime-bearing payloads silently never persisted."""
    cache = SqliteCache(str(tmp_path / "c.db"), strict=True)
    payload = [{"timestamp": datetime(2026, 1, 2), "value": 1.5}]
    await cache.set("fred", payload, ttl_s=60)
    assert await cache.get("fred") == payload
    await cache.close()


async def test_sqlite_expiry(tmp_path):
    cache = SqliteCache(str(tmp_path / "c.db"), strict=True)
    await cache.set("k", {"v": 1}, ttl_s=0)
    assert await cache.get("k") is None
    assert await cache.get_stale("k") == {"v": 1}
    await cache.close()


async def test_namespaced_cache_prefixes_and_digests():
    backend = MemoryCache(strict=True)
    ns_a = NamespacedCache(backend, "app-a", "yahoo")
    ns_b = NamespacedCache(backend, "app-b", "yahoo")

    key_a = ns_a.key("historical_prices", "AAPL", "1d")
    key_b = ns_b.key("historical_prices", "AAPL", "1d")
    assert key_a.startswith("app-a:yahoo:historical_prices:")
    assert key_b.startswith("app-b:yahoo:historical_prices:")
    assert key_a != key_b  # multi-app isolation (v1 ignored the namespace)

    await ns_a.set(key_a, {"v": "a"}, ttl_s=60)
    assert await ns_b.get(key_b) is None
    assert await ns_a.get(key_a) == {"v": "a"}
