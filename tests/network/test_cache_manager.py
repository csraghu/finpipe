import pytest
from finpipe.core.config import CacheConfig
from finpipe.core.exceptions import FinpipeConfigError
from finpipe.network.cache import create_cache_backend
from finpipe.network.cache_manager import CacheManager, resolve_cache_backend


@pytest.fixture(autouse=True)
def reset_cache_manager():
    CacheManager.reset()
    yield
    CacheManager.reset()


def test_cache_manager_non_singleton_creates_fresh_backend():
    config = CacheConfig(cache_type="memory", singleton=False)
    first = CacheManager.get_shared(config)
    second = CacheManager.get_shared(config)
    assert first is not second


def test_cache_manager_singleton_reuses_backend():
    config = CacheConfig(cache_type="memory", singleton=True, namespace="test-ns")
    first = CacheManager.get_shared(config)
    second = CacheManager.get_shared(config)
    assert first is second


def test_cache_manager_sqlite_thread_safety_failure(monkeypatch, tmp_path):
    config = CacheConfig(
        cache_type="sqlite",
        sqlite_path=str(tmp_path / "cache.db"),
        singleton=True,
    )

    class BrokenBackend:
        def verify_thread_safe(self) -> bool:
            return False

    monkeypatch.setattr(
        "finpipe.network.cache_manager.create_cache_backend",
        lambda _cfg: BrokenBackend(),
    )
    with pytest.raises(FinpipeConfigError, match="concurrency self-test"):
        CacheManager.get_shared(config)


def test_create_cache_backend_variants():
    noop = create_cache_backend(CacheConfig(cache_type="none"))
    assert noop.get("missing") is None
    noop.set("k", "v", 60)
    noop.close()

    memory = create_cache_backend(CacheConfig(cache_type="memory", maxsize=10))
    memory.set("k", "v", 60)
    assert memory.get("k") == "v"
    memory.close()


def test_cache_manager_shutdown_closes_sqlite_backend(tmp_path):
    config = CacheConfig(
        cache_type="sqlite",
        sqlite_path=str(tmp_path / "cache.db"),
        singleton=True,
    )
    backend = CacheManager.get_shared(config)
    backend.set("k", "v", 60)
    assert backend._conn is not None  # type: ignore
    CacheManager.shutdown()
    assert not CacheManager._instances
    assert backend._conn is None  # type: ignore


def test_resolve_cache_backend_singleton_shares_backend():
    config = CacheConfig(cache_type="memory", singleton=True, namespace="resolve-shared")
    first = resolve_cache_backend(config)
    second = resolve_cache_backend(config)
    assert first is second


def test_resolve_cache_backend_non_singleton_creates_fresh_backend():
    config = CacheConfig(cache_type="memory", singleton=False)
    first = resolve_cache_backend(config)
    second = resolve_cache_backend(config)
    assert first is not second
