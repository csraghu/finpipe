from __future__ import annotations

import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.health.registry import resolve_probe_keys


def test_capabilities_returns_sorted_handles(config):
    from finpipe.client import Client

    client = Client(config)
    handles = client.catalog.capabilities()

    assert [handle.id for handle in handles] == [
        "equity",
        "intel",
        "llm",
        "macro",
        "options",
        "screener",
    ]


def test_capability_returns_handle_by_id(config):
    from finpipe.client import Client

    client = Client(config)
    equity = client.catalog.capability("equity")

    assert equity.id == "equity"
    assert equity.describe()["label"] == "Equity market data"


def test_capability_unknown_raises(config):
    from finpipe.client import Client

    client = Client(config)
    with pytest.raises(KeyError, match="Unknown capability"):
        client.catalog.capability("crypto")


def test_capability_describe_includes_routing_and_providers(config):
    from finpipe.client import Client

    client = Client(config)
    equity = client.catalog.capability("equity").describe()

    assert equity["client_facade"] == 'client.catalog.capability("equity")'
    assert equity["primary_provider"] == "yahoo"
    assert equity["fallback_provider"] == "alpha_vantage"
    assert "yahoo" in equity["provider_ids"]
    assert "alpha_vantage" in equity["provider_ids"]

    llm = client.catalog.capability("llm").describe()
    assert llm["primary_provider"] == "groq"
    assert llm["fallback_provider"] == "gemini"
    assert set(llm["provider_ids"]) == {"groq", "gemini", "nvidia"}


def test_capability_providers_returns_refs_for_capability(config):
    from finpipe.client import Client

    client = Client(config)
    providers = client.catalog.capability("screener").providers()

    assert providers
    assert all(ref.capability == "screener" for ref in providers)
    assert {ref.provider_id for ref in providers} == {
        "yahoo_trending",
        "yahoo_predefined",
        "finviz",
        "tradingview",
    }


def test_capability_provider_returns_unique_ref(config):
    from finpipe.client import Client

    client = Client(config)
    yahoo_equity = client.catalog.capability("equity").provider("yahoo")
    yahoo_options = client.catalog.capability("options").provider("yahoo")

    assert yahoo_equity.provider_id == yahoo_options.provider_id == "yahoo"
    assert yahoo_equity.capability == "equity"
    assert yahoo_options.capability == "options"
    assert yahoo_equity is not yahoo_options


def test_capability_provider_unknown_raises(config):
    from finpipe.client import Client

    client = Client(config)
    with pytest.raises(KeyError, match="Unknown provider"):
        client.catalog.capability("equity").provider("massive")


def test_capability_routing_resolves_primary_and_fallback(config):
    from finpipe.client import Client

    client = Client(config)
    routing = client.catalog.capability("equity").routing

    assert routing.primary is not None
    assert routing.primary.provider_id == "yahoo"
    assert routing.fallback is not None
    assert routing.fallback.provider_id == "alpha_vantage"


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


@pytest.mark.asyncio
async def test_provider_ref_describe_shapes_capability(config):
    from finpipe.client import Client

    client = Client(config)
    result = await client.catalog.capability("macro").provider("fred").describe()

    assert result["provider_id"] == "fred"
    assert result["capability"] == "macro"


@pytest.mark.asyncio
async def test_catalog_provider_without_health_probe_enabled(config):
    from finpipe.client import Client

    client = Client(config)
    tradingview = client.catalog.capability("screener").provider("tradingview")
    assert tradingview.enabled is True


@pytest.mark.asyncio
async def test_provider_ref_yahoo_equity_vs_options_describe(config):
    from finpipe.client import Client

    client = Client(config)
    equity = await client.catalog.capability("equity").provider("yahoo").describe()
    options = await client.catalog.capability("options").provider("yahoo").describe()

    assert equity["capability"] == "equity"
    assert options["capability"] == "options"
