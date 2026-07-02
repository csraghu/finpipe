"""Resolved-settings dump — DERIVED from the provider manifest registry.

v1 kept a hand-maintained PROVIDER_NAMES tuple here (schwab was already missing
from it) and its own incomplete redaction copy. Both problems are structural
now: providers come from ``REGISTRY``, redaction from ``core.redact``.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.config import FinpipeConfig
from ..core.redact import redact
from ..providers.manifest import REGISTRY
from ..providers.wiring import ensure_provider_modules_loaded

_CAPABILITY_ROUTING = {
    "equity": ("equity_primary", "equity_fallback"),
    "options": ("options_primary", "options_fallback"),
    "llm": ("llm_primary", "llm_fallback"),
}


def dump_settings(config: FinpipeConfig, *, redact_secrets: bool = True) -> dict[str, Any]:
    ensure_provider_modules_loaded()
    routing = config.routing.model_dump(mode="json")

    providers: dict[str, Any] = {}
    capabilities: dict[str, Any] = {}
    for manifest in REGISTRY.all():
        block = getattr(config.providers, manifest.config_attr).model_dump(mode="json")
        providers[manifest.key] = redact(block) if redact_secrets else block

        cap = capabilities.setdefault(
            manifest.capability,
            {
                "providers": [],
                "primary": routing.get(_CAPABILITY_ROUTING.get(manifest.capability, ("", ""))[0]),
                "fallback": routing.get(_CAPABILITY_ROUTING.get(manifest.capability, ("", ""))[1]),
            },
        )
        cap["providers"].append(manifest.key)

    return {
        "dataframe_format": config.dataframe_format,
        "cache": config.cache.model_dump(mode="json"),
        "routing": routing,
        "health": config.health.model_dump(mode="json"),
        "capabilities": capabilities,
        "providers": providers,
    }


def dump_settings_json(config: FinpipeConfig, *, indent: int = 2, redact_secrets: bool = True) -> str:
    return json.dumps(dump_settings(config, redact_secrets=redact_secrets), indent=indent, sort_keys=True)
