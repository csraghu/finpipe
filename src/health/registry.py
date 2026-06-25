from __future__ import annotations

from finpipe.core.config import FinpipeConfig, HealthConfig, ProviderGroupConfig

DEFAULT_PROBE_KEYS: tuple[str, ...] = (
    "equity.yahoo",
    "equity.alpha_vantage",
    "options.massive",
    "options.yahoo",
    "macro.fred",
    "intel.google_news",
    "intel.stocktwits",
    "intel.reddit",
    "screener.yahoo_trending",
    "screener.yahoo_predefined",
    "screener.finviz",
    "screener.tradingview",
    "llm.groq",
    "llm.gemini",
    "llm.nvidia",
)

_PROBE_ENABLED: dict[str, str] = {
    "equity.yahoo": "yahoo",
    "options.yahoo": "yahoo",
    "equity.alpha_vantage": "alpha_vantage",
    "options.massive": "massive",
    "macro.fred": "fred",
    "llm.groq": "groq",
    "llm.gemini": "gemini",
    "llm.nvidia": "nvidia",
}

_INTEL_SOURCES = frozenset({"google_news", "stocktwits", "reddit"})
_SCREENER_SOURCES = frozenset({"yahoo_trending", "yahoo_predefined", "finviz", "tradingview"})


def is_probe_provider_enabled(providers: ProviderGroupConfig, probe_key: str) -> bool:
    return _is_provider_enabled(providers, probe_key)


def _is_provider_enabled(providers: ProviderGroupConfig, probe_key: str) -> bool:
    if probe_key in _PROBE_ENABLED:
        name = _PROBE_ENABLED[probe_key]
        return bool(getattr(providers, name).enabled)

    if probe_key.startswith("intel."):
        source = probe_key.removeprefix("intel.")
        if source not in _INTEL_SOURCES:
            return False
        return providers.sentiment.enabled and getattr(providers.sentiment.sources, source).enabled

    if probe_key.startswith("screener."):
        source = probe_key.removeprefix("screener.")
        if source not in _SCREENER_SOURCES:
            return False
        if not providers.screener.enabled:
            return False
        return getattr(providers.screener.sources, source).enabled

    return False


def resolve_probe_keys(config: FinpipeConfig) -> list[str]:
    """Return probe keys to run based on ``health`` config and provider toggles."""
    health: HealthConfig = config.health
    if not health.enabled:
        return []

    if health.probes:
        return [
            key
            for key in health.probes
            if health.probes[key].enabled and _is_provider_enabled(config.providers, key)
        ]

    return [key for key in DEFAULT_PROBE_KEYS if _is_provider_enabled(config.providers, key)]
