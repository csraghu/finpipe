"""Manifest registry + lazy Client contract tests.

The Client tests run with NO provider API keys set (see conftest) — that is the
point: v1's Client() raised unless every provider was configured (review §2.4).
"""

from __future__ import annotations

import pytest
from finpipe.client import Client
from finpipe.core.config import FinpipeConfig
from finpipe.core.errors import FinpipeConfigError
from finpipe.providers.manifest import REGISTRY, ProviderManifest, ProviderRegistry
from finpipe.providers.wiring import ensure_provider_modules_loaded

EXPECTED_PROVIDERS = {
    "yahoo": "equity",
    "alpha_vantage": "equity",
    "fred": "macro",
    "massive": "options",
    "sentiment": "intel",
    "screener": "screener",
    "groq": "llm",
    "gemini": "llm",
    "nvidia": "llm",
}


def test_registry_contains_all_providers_with_capabilities():
    ensure_provider_modules_loaded()
    for key, capability in EXPECTED_PROVIDERS.items():
        manifest = REGISTRY.get(key)
        assert manifest.capability == capability
        assert manifest.label
        assert manifest.probe


def test_duplicate_registration_raises():
    registry = ProviderRegistry()
    manifest = ProviderManifest(
        key="dup", capability="equity", config_attr="yahoo",
        factory=lambda rt: None, label="Dup",
    )
    registry.add(manifest)
    with pytest.raises(ValueError, match="already registered"):
        registry.add(manifest)


def test_unknown_provider_error_lists_known_keys():
    ensure_provider_modules_loaded()
    with pytest.raises(KeyError, match="yahoo"):
        REGISTRY.get("definitely_not_a_provider")


# --------------------------------------------------------------------------- Client
async def test_client_constructs_with_zero_credentials():
    """v1 regression: no API keys are set, construction must still succeed."""
    async with Client(FinpipeConfig.from_dict({})) as client:
        # typed services materialize lazily without I/O or validation
        assert client.equity is not None
        assert client.options is not None
        assert client.macro is not None
        assert client.intel is not None
        assert client.screener is not None
        assert client.llm is not None
        assert client.catalog.capabilities() == sorted(
            {"equity", "macro", "options", "intel", "screener", "llm"}
        )


async def test_missing_credentials_raise_on_first_use_with_env_var_name():
    async with Client(FinpipeConfig.from_dict({})) as client:
        from datetime import date

        with pytest.raises(FinpipeConfigError, match="FRED_API_KEY"):
            await client.macro.get_macro_series("DGS10", date(2026, 1, 1), date(2026, 1, 31))


async def test_disabled_provider_is_skipped_in_routing_and_blocked_directly():
    config = FinpipeConfig.from_dict({"providers": {"alpha_vantage": {"enabled": False}}})
    async with Client(config) as client:
        assert client._pool.get_if_enabled("alpha_vantage") is None
        with pytest.raises(FinpipeConfigError, match="disabled"):
            client._pool.get("alpha_vantage")


async def test_catalog_is_introspection_only():
    async with Client(FinpipeConfig.from_dict({})) as client:
        ref = client.catalog.provider("fred")
        assert ref.capability == "macro"
        assert ref.required_secrets == ("FRED_API_KEY",)
        assert not hasattr(ref, "get_macro_series")  # no v1-style __getattr__ proxying


async def test_dump_settings_covers_all_manifest_providers():
    async with Client(FinpipeConfig.from_dict({})) as client:
        dumped = client.dump_settings()
        assert set(EXPECTED_PROVIDERS) <= set(dumped["providers"])
        assert dumped["capabilities"]["llm"]["primary"] == "groq"
        assert dumped["capabilities"]["llm"]["fallback"] == "gemini"


async def test_health_reports_unconfigured_not_crash():
    async with Client(FinpipeConfig.from_dict({})) as client:
        report = await client.health_check()
        assert report.results["macro.fred"].status == "unconfigured"
        assert report.http_status in (200, 503)
