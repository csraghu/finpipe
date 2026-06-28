"""Public health-check helpers for application integration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from finpipe.health.models import HealthReport, ProbeResult

if TYPE_CHECKING:
    from finpipe.core.config import FinpipeConfig


async def run_health_check(
    config: FinpipeConfig | None = None,
    *,
    probe_keys: list[str] | None = None,
) -> HealthReport:
    """Run finpipe provider health probes and return an aggregate report.

    Example (FastAPI)::

        report = await run_health_check()
        return JSONResponse(report.to_dict(), status_code=report.http_status)
    """
    from finpipe.client import Client

    async with Client(config) as client:
        if probe_keys is None:
            return await client.health.ping()
        results = await asyncio.gather(*(client.health.ping_probe(key) for key in probe_keys))
        return HealthReport(results={result.key: result for result in results})


async def run_probe(probe_key: str, config: FinpipeConfig | None = None) -> ProbeResult:
    """Run a single provider probe (e.g. ``equity.yahoo``, ``intel.stocktwits``)."""
    from finpipe.client import Client

    async with Client(config) as client:
        return await client.health.ping_probe(probe_key)
