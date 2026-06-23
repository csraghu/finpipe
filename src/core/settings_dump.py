from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from finpipe.core.config import FinpipeConfig

_SECRET_FIELDS = frozenset({"api_key", "access_key_id", "secret_access_key"})
_REDACTED = "<redacted>"

PROVIDER_NAMES: tuple[str, ...] = (
    "yahoo",
    "alpha_vantage",
    "fred",
    "massive",
    "tradingview",
    "screener",
    "sentiment",
    "groq",
    "gemini",
)


@dataclass(frozen=True)
class CapabilitySettings:
    """Maps a Client capability facade to routing and provider configs."""

    name: str
    primary_routing_key: str | None
    fallback_routing_key: str | None
    provider_names: tuple[str, ...]
    protocols: tuple[str, ...]


CAPABILITY_SETTINGS: tuple[CapabilitySettings, ...] = (
    CapabilitySettings(
        "equity",
        "equity_primary",
        "equity_fallback",
        ("yahoo", "alpha_vantage"),
        ("IHistoricalPriceProvider", "IMetadataProvider"),
    ),
    CapabilitySettings(
        "options",
        "options_primary",
        "options_fallback",
        ("massive", "yahoo"),
        ("IOptionsProvider",),
    ),
    CapabilitySettings(
        "macro",
        None,
        None,
        ("fred",),
        ("IMacroProvider",),
    ),
    CapabilitySettings(
        "intel",
        None,
        None,
        ("sentiment",),
        ("IMarketIntelProvider",),
    ),
    CapabilitySettings(
        "screener",
        None,
        None,
        ("screener", "tradingview"),
        ("IScreenerProvider",),
    ),
    CapabilitySettings(
        "llm",
        "llm_primary",
        "llm_fallback",
        ("groq", "gemini"),
        ("ILLMProvider",),
    ),
)


def _redact_secrets(data: Any) -> Any:
    if isinstance(data, dict):
        redacted: dict[str, Any] = {}
        for key, value in data.items():
            if key in _SECRET_FIELDS:
                redacted[key] = _REDACTED if value else None
            else:
                redacted[key] = _redact_secrets(value)
        return redacted
    if isinstance(data, list):
        return [_redact_secrets(item) for item in data]
    return data


def _provider_config_dict(config: FinpipeConfig, provider_name: str) -> dict[str, Any]:
    provider = getattr(config.providers, provider_name)
    return provider.model_dump(mode="json")


def dump_provider_settings(
    config: FinpipeConfig,
    *,
    redact_secrets: bool = True,
) -> dict[str, dict[str, Any]]:
    providers: dict[str, dict[str, Any]] = {}
    for name in PROVIDER_NAMES:
        payload = _provider_config_dict(config, name)
        if redact_secrets:
            payload = _redact_secrets(payload)
        providers[name] = payload
    return providers


def dump_capability_settings(
    config: FinpipeConfig,
    providers: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    provider_settings = providers or dump_provider_settings(config, redact_secrets=False)
    routing = config.routing.model_dump(mode="json")
    capabilities: dict[str, dict[str, Any]] = {}

    for capability in CAPABILITY_SETTINGS:
        entry: dict[str, Any] = {
            "protocols": list(capability.protocols),
            "primary": routing.get(capability.primary_routing_key)
            if capability.primary_routing_key
            else None,
            "fallback": routing.get(capability.fallback_routing_key)
            if capability.fallback_routing_key
            else None,
            "providers": {
                name: provider_settings[name]
                for name in capability.provider_names
                if name in provider_settings
            },
        }
        capabilities[capability.name] = entry

    return capabilities


def dump_settings(
    config: FinpipeConfig,
    *,
    redact_secrets: bool = True,
) -> dict[str, Any]:
    """Return the fully resolved finpipe settings grouped by capability and provider."""
    providers = dump_provider_settings(config, redact_secrets=redact_secrets)
    return {
        "dataframe_format": config.dataframe_format,
        "cache": config.cache.model_dump(mode="json"),
        "routing": config.routing.model_dump(mode="json"),
        "capabilities": dump_capability_settings(config, providers),
        "providers": providers,
    }


def dump_settings_json(
    config: FinpipeConfig,
    *,
    indent: int = 2,
    redact_secrets: bool = True,
) -> str:
    """Serialize resolved settings to a JSON string."""
    return json.dumps(
        dump_settings(config, redact_secrets=redact_secrets),
        indent=indent,
        sort_keys=True,
    )
