import json

import pytest
from finpipe.core.config import CacheConfig, FinpipeConfig
from finpipe.network.cache import SqliteCacheBackend, create_cache_backend


def test_noop_cache_backend():
    backend = create_cache_backend(CacheConfig(cache_type="none"))
    assert backend.get("k") is None
    backend.set("k", "v", 60)
    assert backend.verify_thread_safe() is True


def test_sqlite_verify_thread_safe(tmp_path):
    cache = SqliteCacheBackend(db_path=str(tmp_path / "cache.db"))
    assert cache.verify_thread_safe() is True


def test_in_memory_verify_thread_safe():
    backend = create_cache_backend(FinpipeConfig().cache)
    assert backend.verify_thread_safe() is True


def test_config_load_from_file_and_merge(tmp_path, monkeypatch):
    base = tmp_path / "base.json"
    local = tmp_path / "local.json"
    base.write_text(json.dumps({"dataframe_format": "pandas"}), encoding="utf-8")
    local.write_text(json.dumps({"routing": {"equity_primary": "yahoo"}}), encoding="utf-8")
    cfg = FinpipeConfig.from_file(base, local_path=local)
    assert cfg.dataframe_format == "pandas"
    assert cfg.routing.equity_primary == "yahoo"


def test_config_load_discovery_and_env_override(tmp_path, monkeypatch):
    settings = tmp_path / "finpipe.settings.json"
    settings.write_text(json.dumps({"dataframe_format": "pandas"}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FINPIPE_CACHE_BACKEND", "memory")
    loaded = FinpipeConfig.load()
    assert loaded.dataframe_format == "pandas"


def test_config_dump_settings_json(config):
    payload = config.dump_settings_json(redact_secrets=True)
    assert "providers" in payload


def test_config_unknown_required_key(config):
    with pytest.raises(Exception, match="Unknown required key"):
        config.get_required_key("not_a_key")


def test_core_types_import():
    from finpipe.core import types

    assert types.Interval is not None
    assert types.DataFrameLike is not None
