"""Catalog — introspection ONLY, derived from the provider manifest registry.

Unlike v1, the catalog is not an I/O path: no ``__getattr__`` proxying. Typed
capability services on ``Client`` are the I/O surface; the catalog answers
"what providers exist, how are they routed, what are their settings".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.protocols import IProviderDescribe
from ..providers.manifest import REGISTRY, ProviderManifest
from ..providers.wiring import ensure_provider_modules_loaded

if TYPE_CHECKING:
    from ..client import Client


class ProviderRef:
    """One provider row: manifest metadata + async describe() via the adapter."""

    def __init__(self, client: Client, manifest: ProviderManifest) -> None:
        self._client = client
        self._manifest = manifest

    @property
    def provider_id(self) -> str:
        return self._manifest.key

    @property
    def capability(self) -> str:
        return self._manifest.capability

    @property
    def label(self) -> str:
        return self._manifest.label

    @property
    def description(self) -> str:
        return self._manifest.description

    @property
    def required_secrets(self) -> tuple[str, ...]:
        return self._manifest.secrets

    @property
    def enabled(self) -> bool:
        block = getattr(self._client.config.providers, self._manifest.config_attr)
        return bool(getattr(block, "enabled", True))

    def adapter(self) -> Any:
        """Explicit escape hatch to the underlying adapter (not semver-stable)."""
        return self._client._pool.get(self._manifest.key)

    async def describe(self) -> dict[str, Any]:
        base = {
            "provider_id": self.provider_id,
            "capability": self.capability,
            "label": self.label,
            "enabled": self.enabled,
            "required_secrets": list(self.required_secrets),
        }
        if not self.enabled:
            return base
        adapter = self.adapter()
        if isinstance(adapter, IProviderDescribe):
            return {**(await adapter.describe()), **base}
        return base

    def __repr__(self) -> str:
        return f"ProviderRef({self.capability!r}, {self.provider_id!r}, enabled={self.enabled})"


class CatalogService:
    def __init__(self, client: Client) -> None:
        self._client = client
        ensure_provider_modules_loaded()

    def capabilities(self) -> list[str]:
        return sorted({m.capability for m in REGISTRY.all()})

    def providers(self, capability: str | None = None) -> list[ProviderRef]:
        manifests = REGISTRY.for_capability(capability) if capability else REGISTRY.all()
        return [ProviderRef(self._client, m) for m in sorted(manifests, key=lambda m: m.key)]

    def provider(self, key: str) -> ProviderRef:
        return ProviderRef(self._client, REGISTRY.get(key))

    def routing(self) -> dict[str, Any]:
        return self._client.config.routing.model_dump(mode="json")
