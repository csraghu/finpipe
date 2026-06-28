from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from finpipe.catalog.handles import CapabilityHandle, ProviderRef, adapter_key_for
from finpipe.catalog.registry import PROVIDER_CATALOG
from finpipe.client import Client


def test_adapter_key_for_branches():
    assert adapter_key_for("intel", "google_news") == "sentiment"
    assert adapter_key_for("screener", "tradingview") == "tradingview"
    assert adapter_key_for("screener", "finviz") == "screener"
    assert adapter_key_for("equity", "yahoo") == "yahoo"


def test_catalog_adapter_key_metadata():
    tradingview = next(row for row in PROVIDER_CATALOG if row.provider_id == "tradingview")
    assert tradingview.adapter_key == "tradingview"

    finviz = next(row for row in PROVIDER_CATALOG if row.provider_id == "finviz")
    assert finviz.adapter_key == "screener"

    intel_entries = [row for row in PROVIDER_CATALOG if row.capability == "intel"]
    assert intel_entries
    assert all(row.adapter_key == "sentiment" for row in intel_entries)


def test_provider_ref_routes_via_catalog_adapter_key(config):
    client = Client(config)

    sentiment = MagicMock()
    sentiment.get_news = AsyncMock()
    client._registry._adapters["sentiment"] = sentiment
    intel_ref = client.catalog.capability("intel").provider("google_news")
    assert intel_ref.get_news is sentiment.get_news

    tradingview = MagicMock()
    tradingview.run_screener = AsyncMock(return_value=[])
    screener = MagicMock()
    client._registry._adapters["tradingview"] = tradingview
    client._registry._adapters["screener"] = screener
    tv_ref = client.catalog.capability("screener").provider("tradingview")
    assert tv_ref.run_screener is tradingview.run_screener


def test_provider_ref_properties_and_repr(config):
    client = Client(config)
    entry = next(
        row for row in PROVIDER_CATALOG if row.provider_id == "yahoo" and row.capability == "equity"
    )
    ref = ProviderRef(client, entry, enabled=True)
    assert ref.provider_id == "yahoo"
    assert ref.capability == "equity"
    assert ref.enabled is True
    assert "yahoo" in repr(ref)


@pytest.mark.asyncio
async def test_provider_ref_describe_without_i_provider_describe(config):
    client = Client(config)
    entry = next(
        row for row in PROVIDER_CATALOG if row.provider_id == "yahoo" and row.capability == "equity"
    )
    ref = ProviderRef(client, entry, enabled=True)
    client._registry._adapters["yahoo"] = object()
    payload = await ref.describe()
    assert payload["provider_id"] == "yahoo"
    assert payload["enabled"] is True


def test_provider_ref_delegates_adapter_methods(config):
    client = Client(config)
    entry = next(
        row for row in PROVIDER_CATALOG if row.provider_id == "yahoo" and row.capability == "equity"
    )
    ref = ProviderRef(client, entry, enabled=True)
    mock_adapter = MagicMock()
    mock_adapter.get_metadata = AsyncMock()
    client._registry._adapters["yahoo"] = mock_adapter
    assert ref.get_metadata is mock_adapter.get_metadata


def test_provider_ref_all_properties(config):
    client = Client(config)
    ref = client.catalog.capability("equity").provider("yahoo")
    assert ref.label
    assert ref.description
    assert ref.returns
    assert ref.settings_path
    assert ref.api_surface
    assert repr(ref).startswith("ProviderRef(")


def test_provider_ref_unknown_method_raises(config):
    client = Client(config)
    entry = PROVIDER_CATALOG[0]
    ref = ProviderRef(client, entry, enabled=True)
    with pytest.raises(AttributeError):
        _ = ref.not_a_real_method


def test_capability_handle_routing_and_unknown_provider(config):
    client = Client(config)
    handle = CapabilityHandle(client, "equity")
    routing = handle.routing
    assert routing.primary is not None
    assert routing.primary.provider_id == "yahoo"
    with pytest.raises(KeyError, match="Unknown provider"):
        handle.provider("not_a_provider")


def test_capability_handle_no_composite_raises(config):
    client = Client(config)
    client._composites.pop("llm", None)
    handle = CapabilityHandle(client, "llm")
    with pytest.raises(AttributeError, match="no routed composite"):
        _ = handle.generate_response
