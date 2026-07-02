"""Health probes — derived from provider manifests.

Two probe levels per provider:
- ``configured``: adapter constructs and credential validation passes (no HTTP)
- ``connectivity`` (optional, cheap): one lightweight call per capability

Phase-5 TODO: port v1's richer per-source probes (screener sources, intel
sources) on top of this frame.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.errors import FinpipeConfigError, FinpipeError
from ..providers.manifest import REGISTRY
from ..providers.wiring import ensure_provider_modules_loaded

if TYPE_CHECKING:
    from ..client import Client


@dataclass(frozen=True)
class ProbeResult:
    key: str
    status: str  # connected | configured | unconfigured | disabled | error
    message: str | None = None
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"key": self.key, "status": self.status}
        if self.message:
            out["message"] = self.message
        if self.latency_ms is not None:
            out["latency_ms"] = round(self.latency_ms, 1)
        return out


@dataclass(frozen=True)
class HealthReport:
    results: dict[str, ProbeResult] = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return all(r.status in ("connected", "configured", "disabled") for r in self.results.values())

    @property
    def http_status(self) -> int:
        return 200 if self.healthy else 503

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "results": {key: result.to_dict() for key, result in self.results.items()},
        }


class HealthService:
    def __init__(self, client: Client) -> None:
        self._client = client
        ensure_provider_modules_loaded()

    async def check(self, provider_key: str) -> ProbeResult:
        manifest = REGISTRY.get(provider_key)
        probe_key = manifest.probe or provider_key
        if not self._client.config.health.enabled:
            return ProbeResult(probe_key, "disabled", message="health.enabled is false")
        block = getattr(self._client.config.providers, manifest.config_attr)
        if not getattr(block, "enabled", True):
            return ProbeResult(probe_key, "disabled", message="provider disabled in settings")

        started = time.perf_counter()
        try:
            adapter = self._client._pool.get(provider_key)
            ensure = getattr(adapter, "_ensure_configured", None)
            if callable(ensure):
                ensure()
        except FinpipeConfigError as exc:
            return ProbeResult(probe_key, "unconfigured", message=str(exc))
        except FinpipeError as exc:
            return ProbeResult(probe_key, "error", message=str(exc))
        latency = (time.perf_counter() - started) * 1000
        return ProbeResult(probe_key, "configured", latency_ms=latency)

    async def ping(self) -> HealthReport:
        keys = [m.key for m in REGISTRY.all()]
        results = await asyncio.gather(*(self.check(key) for key in keys))
        return HealthReport(results={r.key: r for r in results})
