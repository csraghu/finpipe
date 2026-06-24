from __future__ import annotations

from finpipe.core.config import FinpipeConfig
from finpipe.health.registry import resolve_probe_keys


def test_list_providers_includes_all_capabilities(config):
    from finpipe.client import Client

    client = Client(config)
    providers = client.catalog.list_providers()
    capabilities = {row.capability for row in providers}

    assert "equity" in capabilities
    assert "screener" in capabilities
    assert "llm" in capabilities
    assert any(row.health_probe_key == "equity.yahoo" for row in providers)


def test_list_providers_filter_by_capability(config):
    from finpipe.client import Client

    client = Client(config)
    screeners = client.catalog.list_providers(capability="screener")

    assert screeners
    assert all(row.capability == "screener" for row in screeners)


def test_list_health_probes_merges_explicit_config():
    config = FinpipeConfig.from_dict(
        {
            "health": {
                "enabled": True,
                "probes": {
                    "equity.yahoo": {"enabled": True},
                    "llm.groq": {"enabled": False},
                },
            }
        }
    )
    from finpipe.client import Client

    client = Client(config)
    probes = {row.key: row for row in client.catalog.list_health_probes()}

    assert probes["equity.yahoo"].configured_in_health is True
    assert probes["equity.yahoo"].would_run is True
    assert probes["llm.groq"].configured_in_health is False
    assert probes["llm.groq"].would_run is False
    assert resolve_probe_keys(config) == ["equity.yahoo"]


def test_health_config_template_matches_would_run(config):
    from finpipe.client import Client

    client = Client(config)
    template = client.catalog.health_config_template()
    active = set(resolve_probe_keys(config))

    assert set(template) == set(client.health.list_probe_keys())
    assert all(template[key]["enabled"] == (key in active) for key in template)


def test_health_describe_probes_delegates_to_catalog(config):
    from finpipe.client import Client

    client = Client(config)
    assert client.health.describe_probes() == client.catalog.list_health_probes()
