"""Targeted tests to reach 95% branch/line coverage."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from finpipe._internal.limits import ProviderHardLimit
from finpipe.core.config import CacheConfig, FinpipeConfig
from finpipe.core.exceptions import FinpipeConfigError, FinpipeDataNotFoundError
from finpipe.core.models import TickerMetadata
from finpipe.core.registry import BuildContext
from finpipe.core.screener_parsers import (
    parse_tradingview_scan_symbols,
    parse_yahoo_quote_payload,
    parse_yahoo_trending_symbols,
)
from finpipe.health import probes
from finpipe.network.cache import SqliteCacheBackend, create_cache_backend
from finpipe.providers.massive import MassiveOptionsAdapter, build_massive
from finpipe.providers.screener import ScreenerAdapter


def test_provider_hard_limit_rpm_only():
    limit = ProviderHardLimit("test", max_rpm=60)
    assert limit.hard_cap_rps == 1.0


def test_screener_parsers_non_dict_inputs():
    assert parse_yahoo_quote_payload([]) == set()
    assert parse_yahoo_trending_symbols("bad") == []
    assert parse_tradingview_scan_symbols(None) == []


def test_screener_parsers_tradingview_without_exchange():
    data = {"data": [{"d": ["MSFT"]}]}
    assert parse_tradingview_scan_symbols(data) == ["MSFT"]


def test_sqlite_cache_get_missing_key(tmp_path):
    cache = SqliteCacheBackend(db_path=str(tmp_path / "cache.db"))
    assert cache.get("absent") is None


def test_sqlite_cache_pragma_operational_error(tmp_path, monkeypatch):
    real_connect = sqlite3.connect

    class ConnWrapper:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def execute(self, sql: str, *params: object):
            if "PRAGMA" in str(sql):
                raise sqlite3.OperationalError("locked")
            return self._conn.execute(sql, *params)

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

    monkeypatch.setattr(
        "finpipe.network.cache.sqlite3.connect",
        lambda *args, **kwargs: ConnWrapper(real_connect(*args, **kwargs)),
    )
    SqliteCacheBackend(db_path=str(tmp_path / "pragma.db"))
    assert (tmp_path / "pragma.db").is_file()


def test_sqlite_cache_get_json_error(tmp_path):
    db_path = tmp_path / "bad_json.db"
    cache = SqliteCacheBackend(db_path=str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO finpipe_cache (key, value, expiry_timestamp) VALUES (?, ?, ?)",
        ("bad", "not-json", 9_999_999_999.0),
    )
    conn.commit()
    conn.close()
    assert cache.get("bad") is None


def test_sqlite_cache_set_failure(tmp_path, monkeypatch):
    cache = SqliteCacheBackend(db_path=str(tmp_path / "set_fail.db"))
    monkeypatch.setattr(
        "finpipe.network.cache.json.dumps",
        MagicMock(side_effect=TypeError("cannot serialize")),
    )
    cache.set("k", object(), 60)


def test_sqlite_cache_verify_thread_safe_errors(tmp_path):
    cache = SqliteCacheBackend(db_path=str(tmp_path / "thread.db"))
    original_set = cache.set

    def flaky_set(key, value, ttl_seconds):
        if key.endswith("-5"):
            raise RuntimeError("boom")
        return original_set(key, value, ttl_seconds)

    with patch.object(cache, "set", side_effect=flaky_set):
        assert cache.verify_thread_safe() is False


def test_create_cache_backend_sqlite_db_path(tmp_path):
    db_path = str(tmp_path / "alt.db")
    backend = create_cache_backend(CacheConfig(cache_type="sqlite", sqlite_db_path=db_path))
    backend.set("k", 1, 60)
    assert backend.get("k") == 1


@pytest.mark.asyncio
async def test_screener_headers_custom_and_default():
    cfg = FinpipeConfig.from_dict(
        {
            "providers": {
                "screener": {
                    "sources": {
                        "yahoo_trending": {
                            "enabled": True,
                            "http": {"user_agent": "custom-screener-agent"},
                        }
                    }
                }
            }
        }
    )
    adapter = ScreenerAdapter(cfg)
    with respx.mock:
        route = respx.get("https://query1.finance.yahoo.com/v1/finance/trending/US").mock(
            return_value=httpx.Response(
                200, json={"finance": {"result": [{"quotes": [{"symbol": "AAPL"}]}]}}
            )
        )
        await adapter.get_trending()
        assert route.calls[0].request.headers["User-Agent"] == "custom-screener-agent"

    cfg2 = FinpipeConfig.from_dict(
        {
            "providers": {
                "screener": {
                    "sources": {
                        "yahoo_trending": {"enabled": True, "http": {}},
                    }
                }
            }
        }
    )
    adapter2 = ScreenerAdapter(cfg2)
    with respx.mock:
        route2 = respx.get("https://query1.finance.yahoo.com/v1/finance/trending/US").mock(
            return_value=httpx.Response(
                200, json={"finance": {"result": [{"quotes": [{"symbol": "AAPL"}]}]}}
            )
        )
        await adapter2.get_trending()
        assert "Mozilla" in route2.calls[0].request.headers["User-Agent"]
    await adapter.close()
    await adapter2.close()


@pytest.mark.asyncio
async def test_screener_disabled_sources_and_cache_hits(config):
    cfg = FinpipeConfig.from_dict(
        {
            "providers": {
                "screener": {
                    "sources": {
                        "yahoo_predefined": {"enabled": False},
                        "finviz": {"enabled": False},
                    }
                }
            }
        }
    )
    adapter = ScreenerAdapter(cfg)
    assert await adapter.get_predefined("day_gainers") == []
    assert await adapter.get_fundamental("ta_topgainers") == []

    adapter_enabled = ScreenerAdapter(config)
    adapter_enabled._cache.set("screener_src_finviz_ta_topgainers", ["AMD"], 60)
    assert await adapter_enabled.get_fundamental("ta_topgainers") == ["AMD"]

    adapter_enabled._cache.set(
        "screener_src_tradingview_" + str(sorted({"limit": 1, "filter": []}.items())),
        ["MSFT"],
        60,
    )
    assert await adapter_enabled.run_tradingview({"limit": 1, "filter": []}) == ["MSFT"]
    await adapter.close()
    await adapter_enabled.close()


@pytest.mark.asyncio
async def test_massive_api_key_and_s3_session_paths(config):
    adapter = MassiveOptionsAdapter(config)
    assert adapter.api_key == config.providers.massive.api_key
    adapter._provider_config = MagicMock(access_key_id=None, secret_access_key=None)
    assert adapter._get_aioboto3_session() is None
    assert await adapter.list_s3_files("prefix/") == []
    await adapter.close()


@pytest.mark.asyncio
async def test_massive_get_options_chain_empty_payload(config):
    adapter = MassiveOptionsAdapter(config)
    with respx.mock:
        respx.get("https://api.massive.com/v1/options/chain").mock(
            return_value=httpx.Response(200, json={})
        )
        with pytest.raises(FinpipeDataNotFoundError, match="No option chain"):
            await adapter.get_options_chain("AAPL")
    await adapter.close()


@pytest.mark.asyncio
async def test_massive_get_options_snapshot_request_failure(config):
    adapter = MassiveOptionsAdapter(config)
    with respx.mock:
        respx.get("https://api.massive.com/v1/options/snapshot").mock(
            side_effect=httpx.ConnectError("down")
        )
        with pytest.raises(FinpipeDataNotFoundError):
            await adapter.get_options_snapshot("AAPL", limit=1)
    await adapter.close()


def test_build_massive_factory(config):
    assert isinstance(build_massive(BuildContext(config=config)), MassiveOptionsAdapter)


@pytest.mark.asyncio
async def test_probe_empty_result_paths(config):
    from finpipe.client import Client

    async with Client(config) as client:
        client._registry.get("alpha_vantage").get_metadata = AsyncMock(
            return_value=TickerMetadata(symbol="")
        )
        assert await probes.probe_equity_alpha_vantage(client, "SPY") == "metadata missing symbol"

        client._registry.get("sentiment").get_social_posts = AsyncMock(return_value=[])
        assert await probes.probe_intel_stocktwits(client, "SPY") == "no stocktwits posts returned"

        client._registry.get("screener").get_predefined = AsyncMock(return_value=[])
        assert (
            await probes.probe_screener_yahoo_predefined(client, "SPY")
            == "predefined screener returned no tickers"
        )

        client._registry.get("screener").run_tradingview = AsyncMock(return_value=[])
        assert (
            await probes.probe_screener_tradingview(client, "SPY")
            == "tradingview screener returned no tickers"
        )

        empty_models = {"details": {"models": []}}
        client._registry.get("gemini").describe = AsyncMock(return_value=empty_models)
        assert await probes.probe_llm_gemini(client, "SPY") == "gemini models list empty"

        client._registry.get("nvidia").describe = AsyncMock(return_value=empty_models)
        assert await probes.probe_llm_nvidia(client, "SPY") == "nvidia models list empty"


@pytest.mark.asyncio
async def test_probe_missing_api_key_raises():
    client = MagicMock()
    client.config.providers.alpha_vantage.api_key = None
    client.config.providers.gemini.api_key = None
    client.config.providers.nvidia.api_key = None

    with pytest.raises(FinpipeConfigError, match="ALPHA_VANTAGE_API_KEY"):
        await probes.probe_equity_alpha_vantage(client, "SPY")
    with pytest.raises(FinpipeConfigError, match="GEMINI_API_KEY"):
        await probes.probe_llm_gemini(client, "SPY")
    with pytest.raises(FinpipeConfigError, match="NVIDIA_API_KEY"):
        await probes.probe_llm_nvidia(client, "SPY")
