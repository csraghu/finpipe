from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import (
    FinpipeConfigError,
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
async def test_check_handles_config_error():
    config = FinpipeConfig.from_dict(
        {
            "providers": {"alpha_vantage": {"enabled": True}},
            "health": {"enabled": True, "probes": {"equity.alpha_vantage": {"enabled": True}}},
        }
    )
    client = MagicMock(config=config)
    
    # Mock universal probe to raise FinpipeConfigError
    yahoo = MagicMock()
    yahoo.get_historical_prices = AsyncMock(side_effect=FinpipeConfigError("unconfigured"))
    # In universal_probe_runner it checks interfaces, so we have to use side_effect on _registry.get
    client._registry.get = MagicMock(side_effect=FinpipeConfigError("ALPHA_VANTAGE_API_KEY not configured"))

    service = HealthService(client)
    result = await service.check("equity.alpha_vantage")
    assert result.status == "unconfigured"


@pytest.mark.asyncio
async def test_check_handles_provider_down():
    config = FinpipeConfig.from_dict(
        {"health": {"enabled": True, "probes": {"equity.yahoo": {"enabled": True}}}}
    )
    client = MagicMock(config=config)
    
    # Let registry get a mock that raises when called
    from finpipe.core.interfaces import IHistoricalPriceProvider
    provider = MagicMock(spec=IHistoricalPriceProvider)
    provider.get_historical_prices = AsyncMock(side_effect=FinpipeProviderDownError("down"))
    client._registry.get = MagicMock(return_value=provider)
    
    service = HealthService(client)
    result = await service.check("equity.yahoo")
    assert result.status == "error"


@pytest.mark.asyncio
async def test_check_handles_rate_limit():
    config = FinpipeConfig.from_dict(
        {"health": {"enabled": True, "probes": {"equity.yahoo": {"enabled": True}}}}
    )
    client = MagicMock(config=config)
    
    from finpipe.core.interfaces import IHistoricalPriceProvider
    provider = MagicMock(spec=IHistoricalPriceProvider)
    provider.get_historical_prices = AsyncMock(side_effect=FinpipeRateLimitExceededError("slow"))
    client._registry.get = MagicMock(return_value=provider)
    
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
