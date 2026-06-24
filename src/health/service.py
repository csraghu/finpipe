from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from finpipe.core.exceptions import (
    FinpipeConfigError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)
from finpipe.health.models import HealthReport, ProbeResult
from finpipe.health.probes import PROBE_RUNNERS
from finpipe.health.registry import resolve_probe_keys

if TYPE_CHECKING:
    from finpipe.catalog.models import HealthProbeCatalogEntryResolved
    from finpipe.client import Client

logger = logging.getLogger(__name__)


class HealthService:
    """Run lightweight connectivity probes against finpipe providers."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self._config = client.config

    def list_probe_keys(self) -> list[str]:
        return resolve_probe_keys(self._config)

    def describe_probes(self) -> list[HealthProbeCatalogEntryResolved]:
        """Static probe catalog merged with current health config (no HTTP)."""
        return self._client.catalog.list_health_probes()

    def health_config_template(self) -> dict[str, object]:
        """Suggested ``health.probes`` block for finpipe.settings.json."""
        return self._client.catalog.health_config_template()

    async def check(self, probe_key: str) -> ProbeResult:
        if not self._config.health.enabled:
            return ProbeResult(probe_key, "disabled", message="health.enabled is false")
        if probe_key not in resolve_probe_keys(self._config):
            return ProbeResult(
                probe_key,
                "skipped",
                message="probe not configured or provider disabled",
            )
        started = time.perf_counter()
        try:
            message = await self._run_probe(probe_key)
            latency_ms = (time.perf_counter() - started) * 1000
            status = "connected" if message is None else "degraded"
            return ProbeResult(probe_key, status, message=message, latency_ms=latency_ms)
        except FinpipeConfigError as exc:
            return ProbeResult(probe_key, "unconfigured", message=str(exc))
        except (FinpipeProviderDownError, FinpipeRateLimitExceededError) as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            return ProbeResult(probe_key, "error", message=str(exc), latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            logger.warning("Health probe %s failed: %s", probe_key, exc)
            return ProbeResult(probe_key, "error", message=str(exc), latency_ms=latency_ms)

    async def check_all(self) -> HealthReport:
        keys = self.list_probe_keys()
        if not keys:
            return HealthReport()
        results = await asyncio.gather(*(self.check(key) for key in keys))
        return HealthReport(results={result.key: result for result in results})

    async def _run_probe(self, probe_key: str) -> str | None:
        runner = PROBE_RUNNERS.get(probe_key)
        if runner is None:
            raise ValueError(f"Unknown health probe key: {probe_key}")
        return await runner(self._client, self._config.health.probe_symbol)
