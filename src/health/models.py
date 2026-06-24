from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProbeStatus = Literal["connected", "degraded", "unconfigured", "error", "disabled", "skipped"]


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single provider health probe."""

    key: str
    status: ProbeStatus
    message: str | None = None
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": self.key, "status": self.status}
        if self.message is not None:
            payload["message"] = self.message
        if self.latency_ms is not None:
            payload["latency_ms"] = round(self.latency_ms, 2)
        return payload


@dataclass
class HealthReport:
    """Aggregated results from ``HealthService.check_all``."""

    results: dict[str, ProbeResult] = field(default_factory=dict)

    @property
    def all_connected(self) -> bool:
        return all(r.status == "connected" for r in self.results.values())

    @property
    def has_errors(self) -> bool:
        return any(r.status in ("error", "degraded") for r in self.results.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_connected": self.all_connected,
            "has_errors": self.has_errors,
            "probes": {key: result.to_dict() for key, result in sorted(self.results.items())},
        }
