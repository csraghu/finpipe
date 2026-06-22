import time

from finpipe.network.cache import InMemoryTTLCache, SqliteCacheBackend


def test_in_memory_cache():
    cache = InMemoryTTLCache(maxsize=10)
    cache.set("key1", "val1", ttl_seconds=1)
    assert cache.get("key1") == "val1"

    # Test expiration
    cache.set("key2", "val2", ttl_seconds=0.1)
    time.sleep(0.2)
    assert cache.get("key2") is None


def test_sqlite_cache(tmp_path):
    db_path = tmp_path / "test_cache.db"
    cache = SqliteCacheBackend(db_path=str(db_path))

    cache.set("key1", {"data": 123}, ttl_seconds=1)
    assert cache.get("key1") == {"data": 123}

    # Test overwrite
    cache.set("key1", {"data": 456}, ttl_seconds=1)
    assert cache.get("key1") == {"data": 456}

    # Test expiration
    cache.set("key2", "val2", ttl_seconds=0)
    time.sleep(0.1)  # just to ensure clock moves
    assert cache.get("key2") is None
