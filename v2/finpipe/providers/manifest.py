"""Single source of truth for providers (review §4: v1 had five parallel lists).

One ``@provider(...)`` decoration per adapter carries everything the rest of the
package needs: construction, catalog rows, health probes, settings dump, secret
requirements, and optional-extra mapping. Catalog/health/dump are DERIVED from
this registry — there is nothing else to keep in sync when adding a provider.

Adding a provider:
1. Write the adapter module with a ``@provider(...)``-decorated factory.
2. Add its config block to ``core/config.py``.
That's it — it appears in the catalog, health probes, and settings dump automatically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .base import ProviderRuntime

Factory = Callable[["ProviderRuntime"], Any]


@dataclass(frozen=True)
class ProviderManifest:
    key: str                      # registry key, also the rate-limit namespace root
    capability: str               # equity | options | macro | intel | screener | llm
    config_attr: str              # attribute on FinpipeConfig.providers
    factory: Factory
    label: str
    description: str = ""
    secrets: tuple[str, ...] = () # env vars required when enabled (validated on first use)
    extra: str | None = None      # pip extra providing heavy deps (import inside factory!)
    probe: str | None = None      # health probe key; None = no probe
    aliases: tuple[str, ...] = field(default=())


class ProviderRegistry:
    def __init__(self) -> None:
        self._manifests: dict[str, ProviderManifest] = {}

    def add(self, manifest: ProviderManifest) -> None:
        if manifest.key in self._manifests:
            raise ValueError(f"Provider already registered: {manifest.key}")
        self._manifests[manifest.key] = manifest

    def get(self, key: str) -> ProviderManifest:
        try:
            return self._manifests[key]
        except KeyError as exc:
            known = ", ".join(sorted(self._manifests))
            raise KeyError(f"Unknown provider {key!r}. Registered: {known}") from exc

    def for_capability(self, capability: str) -> list[ProviderManifest]:
        return [m for m in self._manifests.values() if m.capability == capability]

    def all(self) -> list[ProviderManifest]:
        return list(self._manifests.values())


REGISTRY = ProviderRegistry()


def provider(
    key: str,
    *,
    capability: str,
    config_attr: str,
    label: str,
    description: str = "",
    secrets: tuple[str, ...] = (),
    extra: str | None = None,
    probe: str | None = None,
) -> Callable[[Factory], Factory]:
    """Register a provider factory with its full manifest."""

    def decorator(factory: Factory) -> Factory:
        REGISTRY.add(
            ProviderManifest(
                key=key,
                capability=capability,
                config_attr=config_attr,
                factory=factory,
                label=label,
                description=description,
                secrets=secrets,
                extra=extra,
                probe=probe or f"{capability}.{key}",
            )
        )
        return factory

    return decorator
