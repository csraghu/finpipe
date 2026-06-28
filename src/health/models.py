from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProbeStatus = Literal["connected", "degraded", "unconfigured", "error", "disabled", "skipped"]

# Application health semantics: connected probes map to HTTP 200; failures map to 503.
_PROBE_HTTP_STATUS: dict[ProbeStatus, int] = {
    "connected": 200,
    "degraded": 503,
    "error": 503,
    "unconfigured": 501,
    "disabled": 204,
    "skipped": 204,
}


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single provider health probe."""

    key: str
    status: ProbeStatus
    message: str | None = None
    latency_ms: float | None = None

    @property
    def ok(self) -> bool:
        """True when the provider returned usable data (HTTP-success semantics)."""
        return self.status == "connected"

    @property
    def http_status(self) -> int:
        """Suggested HTTP status for this probe (200 = success, 503 = failure)."""
        return _PROBE_HTTP_STATUS[self.status]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "status": self.status,
            "ok": self.ok,
            "http_status": self.http_status,
        }
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
    def ok(self) -> bool:
        """True when every actionable probe succeeded (maps to HTTP 200)."""
        actionable = [
            result
            for result in self.results.values()
            if result.status not in ("skipped", "disabled", "unconfigured")
        ]
        if not actionable:
            return True
        return all(result.status == "connected" for result in actionable)

    @property
    def http_status(self) -> int:
        """Aggregate HTTP status for app health endpoints (200 ok, 503 degraded)."""
        return 200 if self.ok else 503

    @property
    def has_errors(self) -> bool:
        return any(r.status in ("error", "degraded") for r in self.results.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "http_status": self.http_status,
            "all_connected": self.all_connected,
            "has_errors": self.has_errors,
            "probes": {key: result.to_dict() for key, result in sorted(self.results.items())},
        }
