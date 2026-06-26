"""Capability and provider handles for the catalog-centric public API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from finpipe.catalog.models import CapabilityName, ProviderCatalogEntry
from finpipe.catalog.registry import CAPABILITY_CATALOG, PROVIDER_CATALOG
from finpipe.core.interfaces import IProviderDescribe
from finpipe.health.registry import is_probe_provider_enabled

if TYPE_CHECKING:
    from finpipe.client import Client


def adapter_key_for(capability: CapabilityName, provider_id: str) -> str:
    if capability == "intel":
        return "sentiment"
    if capability == "screener" and provider_id == "tradingview":
        return "tradingview"
    if capability == "screener":
        return "screener"
    return provider_id


class CapabilityRouting:
    """Primary/fallback provider refs resolved from routing config (v1)."""

    def __init__(
        self,
        *,
        primary: ProviderRef | None,
        fallback: ProviderRef | None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback


class ProviderRef:
    """One catalog provider row with explicit adapter I/O and async describe()."""

    __slots__ = ("_client", "_entry", "_enabled")

    def __init__(
        self,
        client: Client,
        entry: ProviderCatalogEntry,
        *,
        enabled: bool,
    ) -> None:
        self._client = client
        self._entry = entry
        self._enabled = enabled

    @property
    def provider_id(self) -> str:
        return self._entry.provider_id

    @property
    def capability(self) -> CapabilityName:
        return self._entry.capability

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def label(self) -> str:
        return self._entry.label

    @property
    def description(self) -> str:
        return self._entry.description

    @property
    def returns(self) -> str:
        return self._entry.returns

    @property
    def settings_path(self) -> str:
        return self._entry.settings_path

    @property
    def api_surface(self) -> str:
        return self._entry.api_surface

    @property
    def health_probe_key(self) -> str | None:
        return self._entry.health_probe_key

    def _adapter(self) -> Any:
        key = adapter_key_for(self._entry.capability, self._entry.provider_id)
        return self._client._registry.get(key)

    async def describe(self) -> dict[str, Any]:
        adapter = self._adapter()
        if not isinstance(adapter, IProviderDescribe):
            return {
                "provider_id": self.provider_id,
                "capability": self.capability,
                "enabled": self.enabled,
            }
        payload = dict(await adapter.describe())
        payload["provider_id"] = self.provider_id
        payload["capability"] = self.capability
        payload["enabled"] = self.enabled
        return payload

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._adapter(), name)

    def __repr__(self) -> str:
        return (
            f"ProviderRef(capability={self.capability!r}, "
            f"provider_id={self.provider_id!r}, enabled={self.enabled})"
        )


class CapabilityHandle:
    """One capability group with routed composite I/O and provider inventory."""

    __slots__ = ("_client", "id")

    def __init__(self, client: Client, capability: CapabilityName) -> None:
        self._client = client
        self.id = capability

    def describe(self) -> dict[str, Any]:
        entry = next(row for row in CAPABILITY_CATALOG if row.capability == self.id)
        routing = self._client.config.routing.model_dump()
        provider_ids = tuple(
            row.provider_id
            for row in PROVIDER_CATALOG
            if row.capability == self.id
        )
        primary_provider = (
            routing.get(entry.primary_routing_key)
            if entry.primary_routing_key
            else None
        )
        fallback_provider = (
            routing.get(entry.fallback_routing_key)
            if entry.fallback_routing_key
            else None
        )
        payload: dict[str, Any] = {
            "capability": entry.capability,
            "label": entry.label,
            "description": entry.description,
            "client_facade": entry.client_facade,
            "protocols": list(entry.protocols),
            "provider_ids": list(provider_ids),
            "provider_count": len(provider_ids),
        }
        if entry.primary_routing_key is not None:
            payload["primary_routing_key"] = entry.primary_routing_key
            payload["primary_provider"] = primary_provider
        if entry.fallback_routing_key is not None:
            payload["fallback_routing_key"] = entry.fallback_routing_key
            payload["fallback_provider"] = fallback_provider
        return payload

    @property
    def routing(self) -> CapabilityRouting:
        meta = self.describe()
        primary_name = meta.get("primary_provider")
        fallback_name = meta.get("fallback_provider")
        primary = self.provider(primary_name) if primary_name else None
        fallback = None
        if fallback_name and fallback_name != primary_name:
            fallback = self.provider(fallback_name)
        return CapabilityRouting(primary=primary, fallback=fallback)

    def providers(self) -> list[ProviderRef]:
        return self._client.catalog._provider_refs_for(self.id)

    def provider(self, provider_id: str) -> ProviderRef:
        normalized = provider_id.strip().lower()
        for ref in self.providers():
            if ref.provider_id == normalized:
                return ref
        raise KeyError(
            f"Unknown provider {provider_id!r} for capability {self.id!r}"
        )

    def __getattr__(self, name: str) -> Any:
        composite = self._client._composites.get(self.id)
        if composite is None:
            raise AttributeError(
                f"Capability {self.id!r} has no routed composite; "
                f"use ProviderRef methods (e.g. routing.primary)"
            )
        return getattr(composite, name)

    def __repr__(self) -> str:
        return f"CapabilityHandle({self.id!r})"
