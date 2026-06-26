from finpipe.catalog.models import (
    CapabilityCatalogEntry,
    CapabilityCatalogEntryResolved,
    HealthProbeCatalogEntryResolved,
    ProviderCatalogEntry,
    ProviderCatalogEntryResolved,
)
from finpipe.catalog.registry import CAPABILITY_CATALOG, PROBE_CATALOG, PROVIDER_CATALOG


def test_capability_catalog_entry_to_dict_with_routing_keys():
    entry = CAPABILITY_CATALOG[0]
    payload = entry.to_dict()
    assert payload["capability"] == entry.capability
    assert "primary_routing_key" in payload
    assert "fallback_routing_key" in payload


def test_capability_catalog_entry_resolved_round_trip():
    entry = CAPABILITY_CATALOG[1]
    resolved = CapabilityCatalogEntryResolved.from_entry(
        entry,
        primary_provider="massive",
        fallback_provider="yahoo",
        provider_ids=("massive", "yahoo"),
        client_adapters=("massive", "yahoo"),
    )
    payload = resolved.to_dict()
    assert payload["primary_provider"] == "massive"
    assert payload["provider_count"] == 2


def test_provider_catalog_entry_to_dict_with_probe_key():
    entry = next(row for row in PROVIDER_CATALOG if row.health_probe_key)
    payload = entry.to_dict()
    assert payload["health_probe_key"] == entry.health_probe_key


def test_provider_catalog_entry_resolved_to_dict():
    entry = PROVIDER_CATALOG[0]
    resolved = ProviderCatalogEntryResolved.from_entry(
        entry,
        provider_enabled=True,
        health_probe_enabled=True,
        health_probe_would_run=True,
    )
    payload = resolved.to_dict()
    assert payload["provider_enabled"] is True
    assert payload["health_probe_would_run"] is True


def test_health_probe_catalog_entry_to_dict():
    entry = PROBE_CATALOG[0]
    payload = entry.to_dict()
    assert payload["key"] == entry.key
    assert payload["probe_action"]


def test_health_probe_catalog_entry_resolved_to_dict():
    entry = PROBE_CATALOG[0]
    resolved = HealthProbeCatalogEntryResolved.from_entry(
        entry,
        provider_enabled=True,
        configured_in_health=True,
        would_run=True,
    )
    assert resolved.to_dict()["would_run"] is True


def test_catalog_entries_without_optional_keys():
    bare_capability = CapabilityCatalogEntry(
        capability="macro",
        label="Macro",
        description="desc",
        client_facade="facade",
        protocols=("IMacroProvider",),
    )
    assert "primary_routing_key" not in bare_capability.to_dict()

    bare_provider = ProviderCatalogEntry(
        provider_id="x",
        capability="macro",
        label="X",
        description="d",
        returns="r",
        settings_path="s",
        api_surface="a",
    )
    assert "health_probe_key" not in bare_provider.to_dict()
