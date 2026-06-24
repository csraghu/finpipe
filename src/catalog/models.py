from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CapabilityName = Literal[
    "equity",
    "options",
    "macro",
    "intel",
    "screener",
    "llm",
]


@dataclass(frozen=True)
class ProviderCatalogEntry:
    """Static description of one finpipe provider or screener/intel source."""

    provider_id: str
    capability: CapabilityName
    label: str
    description: str
    returns: str
    settings_path: str
    api_surface: str
    health_probe_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider_id": self.provider_id,
            "capability": self.capability,
            "label": self.label,
            "description": self.description,
            "returns": self.returns,
            "settings_path": self.settings_path,
            "api_surface": self.api_surface,
        }
        if self.health_probe_key is not None:
            payload["health_probe_key"] = self.health_probe_key
        return payload


@dataclass(frozen=True)
class ProviderCatalogEntryResolved:
    """Provider catalog row merged with the active ``FinpipeConfig``."""

    provider_id: str
    capability: CapabilityName
    label: str
    description: str
    returns: str
    settings_path: str
    api_surface: str
    health_probe_key: str | None
    provider_enabled: bool
    health_probe_enabled: bool | None
    health_probe_would_run: bool | None

    @classmethod
    def from_entry(
        cls,
        entry: ProviderCatalogEntry,
        *,
        provider_enabled: bool,
        health_probe_enabled: bool | None,
        health_probe_would_run: bool | None,
    ) -> ProviderCatalogEntryResolved:
        return cls(
            provider_id=entry.provider_id,
            capability=entry.capability,
            label=entry.label,
            description=entry.description,
            returns=entry.returns,
            settings_path=entry.settings_path,
            api_surface=entry.api_surface,
            health_probe_key=entry.health_probe_key,
            provider_enabled=provider_enabled,
            health_probe_enabled=health_probe_enabled,
            health_probe_would_run=health_probe_would_run,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "provider_id": self.provider_id,
            "capability": self.capability,
            "label": self.label,
            "description": self.description,
            "returns": self.returns,
            "settings_path": self.settings_path,
            "api_surface": self.api_surface,
            "provider_enabled": self.provider_enabled,
            "health_probe_enabled": self.health_probe_enabled,
            "health_probe_would_run": self.health_probe_would_run,
        }
        if self.health_probe_key is not None:
            payload["health_probe_key"] = self.health_probe_key
        return payload


@dataclass(frozen=True)
class HealthProbeCatalogEntry:
    """Static description of one ``client.health`` probe key."""

    key: str
    capability: CapabilityName
    provider_id: str
    label: str
    description: str
    returns: str
    probe_action: str
    settings_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "capability": self.capability,
            "provider_id": self.provider_id,
            "label": self.label,
            "description": self.description,
            "returns": self.returns,
            "probe_action": self.probe_action,
            "settings_path": self.settings_path,
        }


@dataclass(frozen=True)
class HealthProbeCatalogEntryResolved:
    """Probe catalog row merged with the active ``FinpipeConfig``."""

    key: str
    capability: CapabilityName
    provider_id: str
    label: str
    description: str
    returns: str
    probe_action: str
    settings_path: str
    provider_enabled: bool
    configured_in_health: bool
    would_run: bool

    @classmethod
    def from_entry(
        cls,
        entry: HealthProbeCatalogEntry,
        *,
        provider_enabled: bool,
        configured_in_health: bool,
        would_run: bool,
    ) -> HealthProbeCatalogEntryResolved:
        return cls(
            key=entry.key,
            capability=entry.capability,
            provider_id=entry.provider_id,
            label=entry.label,
            description=entry.description,
            returns=entry.returns,
            probe_action=entry.probe_action,
            settings_path=entry.settings_path,
            provider_enabled=provider_enabled,
            configured_in_health=configured_in_health,
            would_run=would_run,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "capability": self.capability,
            "provider_id": self.provider_id,
            "label": self.label,
            "description": self.description,
            "returns": self.returns,
            "probe_action": self.probe_action,
            "settings_path": self.settings_path,
            "provider_enabled": self.provider_enabled,
            "configured_in_health": self.configured_in_health,
            "would_run": self.would_run,
        }
