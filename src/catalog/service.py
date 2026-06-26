from __future__ import annotations

from typing import TYPE_CHECKING

from finpipe.catalog.handles import CapabilityHandle, ProviderRef
from finpipe.catalog.models import (
    CAPABILITY_GROUPS,
    CapabilityName,
    HealthProbeCatalogEntryResolved,
    ProviderCatalogEntry,
)
from finpipe.catalog.registry import PROBE_CATALOG, PROVIDER_CATALOG
from finpipe.health.registry import is_probe_provider_enabled, resolve_probe_keys

if TYPE_CHECKING:
    from finpipe.client import Client


class CatalogService:
    """Read-only capability/provider inventory and health probe catalog."""

    def __init__(self, client: Client) -> None:
        self._client = client
        self._config = client.config

    def capabilities(self) -> list[CapabilityHandle]:
        """Return all capability handles sorted alphabetically by id."""
        return sorted(
            (CapabilityHandle(self._client, name) for name in CAPABILITY_GROUPS),
            key=lambda handle: handle.id,
        )

    def capability(self, name: CapabilityName | str) -> CapabilityHandle:
        """Return one capability handle by id."""
        normalized = str(name).strip().lower()
        if normalized not in CAPABILITY_GROUPS:
            valid = ", ".join(CAPABILITY_GROUPS)
            raise KeyError(f"Unknown capability {name!r}. Valid capabilities: {valid}")
        return CapabilityHandle(self._client, normalized)  # type: ignore[arg-type]

    def _provider_enabled(self, entry: ProviderCatalogEntry) -> bool:
        probe_key = entry.health_probe_key
        if probe_key is None:
            return True
        return is_probe_provider_enabled(self._config.providers, probe_key)

    def _provider_refs_for(self, capability: CapabilityName) -> list[ProviderRef]:
        rows: list[ProviderRef] = []
        for entry in PROVIDER_CATALOG:
            if entry.capability != capability:
                continue
            rows.append(
                ProviderRef(
                    self._client,
                    entry,
                    enabled=self._provider_enabled(entry),
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

    def health_config_template(self) -> dict[str, dict[str, bool]]:
        """Suggested ``health.probes`` block for finpipe.settings.json."""
        return {probe.key: {"enabled": probe.would_run} for probe in self.list_health_probes()}
