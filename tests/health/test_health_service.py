from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.core.models import TickerMetadata
from finpipe.health.registry import resolve_probe_keys
from finpipe.health.service import HealthService


def test_resolve_probe_keys_uses_explicit_probes():
    config = FinpipeConfig.from_dict(
        {
            "health": {
                "enabled": True,
                "probes": {
                    "equity.yahoo": {"enabled": True},
                    "llm.groq": {"enabled": False},
                    "screener.yahoo_trending": {"enabled": True},
                },
            }
        }
    )
    keys = resolve_probe_keys(config)
    assert keys == ["equity.yahoo", "screener.yahoo_trending"]


def test_resolve_probe_keys_empty_when_disabled():
    config = FinpipeConfig.from_dict({"health": {"enabled": False}})
    assert resolve_probe_keys(config) == []


@pytest.mark.asyncio
async def test_check_all_runs_configured_probes():
    config = FinpipeConfig.from_dict(
        {
            "health": {
                "enabled": True,
                "probes": {"equity.yahoo": {"enabled": True}},
            }
        }
    )
    client = MagicMock()
    client.config = config
    client.yahoo.get_metadata = AsyncMock(
        return_value=TickerMetadata(symbol="SPY", short_name="SPDR")
    )

    service = HealthService(client)
    report = await service.check_all()

    assert report.all_connected
    assert report.results["equity.yahoo"].status == "connected"
    client.yahoo.get_metadata.assert_awaited_once_with("SPY")


@pytest.mark.asyncio
async def test_check_marks_degraded_when_probe_returns_message():
    config = FinpipeConfig.from_dict(
        {
            "health": {
                "enabled": True,
                "probes": {"screener.yahoo_trending": {"enabled": True}},
            }
        }
    )
    client = MagicMock()
    client.config = config
    client.screener.get_trending = AsyncMock(return_value=[])

    service = HealthService(client)
    result = await service.check("screener.yahoo_trending")

    assert result.status == "degraded"
    assert result.message == "trending screener returned no tickers"


@pytest.mark.asyncio
async def test_check_skips_unconfigured_probe_key():
    config = FinpipeConfig.from_dict(
        {
            "health": {
                "enabled": True,
                "probes": {"equity.yahoo": {"enabled": True}},
            }
        }
    )
    client = MagicMock()
    client.config = config
    service = HealthService(client)
    result = await service.check("llm.groq")
    assert result.status == "skipped"
