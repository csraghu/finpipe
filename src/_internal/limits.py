"""Documented hard rate ceilings per provider namespace (ported from aksh)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderHardLimit:
    namespace: str
    max_requests_per_second: float | None = None
    max_rpm: int | None = None

    @property
    def hard_cap_rps(self) -> float:
        if self.max_requests_per_second is not None:
            return self.max_requests_per_second
        if self.max_rpm is not None:
            return self.max_rpm / 60.0
        return 1.0


DEFAULT_PROVIDER_HARD_LIMITS: dict[str, ProviderHardLimit] = {
    "yfinance": ProviderHardLimit("yfinance", max_requests_per_second=10.0),
    "yahoo": ProviderHardLimit("yahoo", max_requests_per_second=2.0),
    "alpha_vantage": ProviderHardLimit("alpha_vantage", max_requests_per_second=0.083),
    "fred": ProviderHardLimit("fred", max_requests_per_second=2.0),
    "massive": ProviderHardLimit("massive", max_requests_per_second=5.0),
    "groq": ProviderHardLimit("groq", max_rpm=30),
    "gemini": ProviderHardLimit("gemini", max_rpm=60),
    "nvidia": ProviderHardLimit("nvidia", max_rpm=60),
    "stocktwits": ProviderHardLimit("stocktwits", max_rpm=60),
    "google_news": ProviderHardLimit("google_news", max_requests_per_second=1.0),
    "reddit": ProviderHardLimit("reddit", max_requests_per_second=0.5),
    "screener.tradingview": ProviderHardLimit("screener.tradingview", max_requests_per_second=2.0),
    "screener.yahoo_trending": ProviderHardLimit(
        "screener.yahoo_trending", max_requests_per_second=2.0
    ),
    "screener.yahoo_predefined": ProviderHardLimit(
        "screener.yahoo_predefined", max_requests_per_second=2.0
    ),
    "screener.finviz": ProviderHardLimit("screener.finviz", max_requests_per_second=2.0),
}


def get_hard_cap_rps(namespace: str, configured_cap: float) -> float:
    """Clamp configured cap to documented provider maximum."""
    limit = DEFAULT_PROVIDER_HARD_LIMITS.get(namespace)
    if limit is None:
        return configured_cap
    return min(configured_cap, limit.hard_cap_rps)
