from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.core.interfaces import IHistoricalPriceProvider, IScreenerProvider
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
    assert "equity.yahoo" in keys
    assert "screener.yahoo_trending" in keys
    assert "llm.groq" not in keys


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
    yahoo_provider = MagicMock(spec=IHistoricalPriceProvider)
    yahoo_provider.get_historical_prices = AsyncMock(return_value=[1, 2])

    client = MagicMock()
    client.config = config
    client._registry = MagicMock()
    client._registry.get = MagicMock(return_value=yahoo_provider)

    service = HealthService(client)

    with patch("finpipe.health.service.resolve_probe_keys", return_value=["equity.yahoo"]):
        report = await service.check_all()

    assert report.all_connected
    assert report.results["equity.yahoo"].status == "connected"


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

    screener_provider = MagicMock(spec=IScreenerProvider)
    screener_provider.run_screener = AsyncMock(side_effect=ValueError("bad filter"))

    client = MagicMock()
    client.config = config
    client._registry = MagicMock()
    client._registry.get = MagicMock(return_value=screener_provider)

    service = HealthService(client)
    result = await service.check("screener.yahoo_trending")

    assert result.status == "degraded"
    assert "screener execution failed" in result.message


@pytest.mark.asyncio
async def test_check_skips_unconfigured_probe_key():
    config = FinpipeConfig.from_dict(
        {
            "health": {
                "enabled": True,
                "probes": {"equity.yahoo": {"enabled": False}},
            }
        }
    )
    client = MagicMock(config=config)
    service = HealthService(client)

    result = await service.check("equity.yahoo")
    assert result.status == "skipped"
