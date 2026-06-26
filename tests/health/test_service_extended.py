from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import (
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)
from finpipe.health.service import HealthService


@pytest.mark.asyncio
async def test_check_when_health_disabled():
    config = FinpipeConfig.from_dict({"health": {"enabled": False}})
    service = HealthService(MagicMock(config=config))
    result = await service.check("equity.yahoo")
    assert result.status == "disabled"


@pytest.mark.asyncio
async def test_check_all_returns_empty_when_no_probes():
    config = FinpipeConfig.from_dict({"health": {"enabled": False}})
    client = MagicMock(config=config)
    service = HealthService(client)
    report = await service.check_all()
    assert report.results == {}


@pytest.mark.asyncio
async def test_check_handles_config_error(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    config = FinpipeConfig.from_dict(
        {
            "providers": {"alpha_vantage": {"enabled": True}},
            "health": {"enabled": True, "probes": {"equity.alpha_vantage": {"enabled": True}}},
        }
    )
    client = MagicMock(config=config)
    service = HealthService(client)
    result = await service.check("equity.alpha_vantage")
    assert result.status == "unconfigured"


@pytest.mark.asyncio
async def test_check_handles_provider_down():
    config = FinpipeConfig.from_dict(
        {"health": {"enabled": True, "probes": {"equity.yahoo": {"enabled": True}}}}
    )
    client = MagicMock(config=config)
    yahoo = MagicMock()
    yahoo.get_metadata = AsyncMock(side_effect=FinpipeProviderDownError("down"))
    equity = MagicMock()
    equity.provider = MagicMock(return_value=yahoo)
    client.catalog.capability = MagicMock(return_value=equity)
    service = HealthService(client)
    result = await service.check("equity.yahoo")
    assert result.status == "error"


@pytest.mark.asyncio
async def test_check_handles_rate_limit():
    config = FinpipeConfig.from_dict(
        {"health": {"enabled": True, "probes": {"equity.yahoo": {"enabled": True}}}}
    )
    client = MagicMock(config=config)
    yahoo = MagicMock()
    yahoo.get_metadata = AsyncMock(side_effect=FinpipeRateLimitExceededError("slow"))
    equity = MagicMock()
    equity.provider = MagicMock(return_value=yahoo)
    client.catalog.capability = MagicMock(return_value=equity)
    service = HealthService(client)
    result = await service.check("equity.yahoo")
    assert result.status == "error"


@pytest.mark.asyncio
async def test_run_probe_unknown_raises():
    config = FinpipeConfig.from_dict(
        {"health": {"enabled": True, "probes": {"equity.yahoo": {"enabled": True}}}}
    )
    client = MagicMock(config=config)
    service = HealthService(client)
    with pytest.raises(ValueError, match="Unknown health probe"):
        await service._run_probe("not.real")
