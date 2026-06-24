from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from finpipe.catalog.models import (
    HealthProbeCatalogEntryResolved,
    ProviderCatalogEntryResolved,
)
from finpipe.catalog.registry import PROBE_CATALOG, PROVIDER_CATALOG
from finpipe.health.registry import is_probe_provider_enabled, resolve_probe_keys

if TYPE_CHECKING:
    from finpipe.client import Client

CapabilityFilter = Literal["equity", "options", "macro", "intel", "screener", "llm"] | None


class CatalogService:
    """Read-only provider and health-probe inventory (no external HTTP)."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self._config = client.config

    def list_providers(
        self,
        *,
        capability: CapabilityFilter = None,
    ) -> list[ProviderCatalogEntryResolved]:
        active_probe_keys = set(resolve_probe_keys(self._config))
        health = self._config.health
        explicit_probes = bool(health.probes)

        rows: list[ProviderCatalogEntryResolved] = []
        for entry in PROVIDER_CATALOG:
            if capability is not None and entry.capability != capability:
                continue
            probe_key = entry.health_probe_key
            provider_enabled = is_probe_provider_enabled(self._config.providers, probe_key)
            health_probe_enabled: bool | None = None
            health_probe_would_run: bool | None = None
            if probe_key is not None:
                if explicit_probes:
                    toggle = health.probes.get(probe_key)
                    health_probe_enabled = toggle.enabled if toggle is not None else False
                else:
                    health_probe_enabled = provider_enabled
                health_probe_would_run = probe_key in active_probe_keys

            rows.append(
                ProviderCatalogEntryResolved.from_entry(
                    entry,
                    provider_enabled=provider_enabled,
                    health_probe_enabled=health_probe_enabled,
                    health_probe_would_run=health_probe_would_run,
                )
            )
        return rows

    def list_health_probes(self) -> list[HealthProbeCatalogEntryResolved]:
        active_probe_keys = set(resolve_probe_keys(self._config))
        health = self._config.health
        explicit_probes = bool(health.probes)

        rows: list[HealthProbeCatalogEntryResolved] = []
        for entry in PROBE_CATALOG:
            provider_enabled = is_probe_provider_enabled(self._config.providers, entry.key)
            if explicit_probes:
                toggle = health.probes.get(entry.key)
                configured_in_health = toggle.enabled if toggle is not None else False
            else:
                configured_in_health = provider_enabled
            rows.append(
                HealthProbeCatalogEntryResolved.from_entry(
                    entry,
                    provider_enabled=provider_enabled,
                    configured_in_health=configured_in_health,
                    would_run=entry.key in active_probe_keys,
                )
            )
        return rows

    def health_config_template(self) -> dict[str, Any]:
        """Suggested ``health.probes`` block for finpipe.settings.json."""
        return {probe.key: {"enabled": probe.would_run} for probe in self.list_health_probes()}
