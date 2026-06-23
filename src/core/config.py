from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from finpipe.core.exceptions import FinpipeConfigError
from pydantic import BaseModel, ConfigDict, Field, model_validator

_REQUIRED_KEYS: dict[str, tuple[str, str]] = {
    "fred_api_key": ("FRED_API_KEY", "fred"),
    "alpha_vantage_api_key": ("ALPHA_VANTAGE_API_KEY", "alpha_vantage"),
    "groq_api_key": ("GROQ_API_KEY", "groq"),
    "gemini_api_key": ("GEMINI_API_KEY", "gemini"),
    "massive_api_key": ("MASSIVE_API_KEY", "massive"),
}


DEFAULT_GROQ_MODEL = "llama3-8b-8192"
DEFAULT_GEMINI_MODEL = "gemini-1.5-flash"


class RateLimitConfig(BaseModel):
    """User-tunable hard limits and HTTP resilience. AIMD tuning is internal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_requests_per_second: float = Field(default=5.0, gt=0)
    max_requests_per_minute: int | None = Field(default=None, ge=1)
    max_tokens_per_minute: int | None = Field(default=None, ge=1)
    max_retries: int = 3
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout_sec: float = 60.0
    backoff_multiplier: float = 1.5


class YahooTTLConfig(BaseModel):
    """TTLs for IHistoricalPriceProvider, IMetadataProvider, IOptionsProvider (Yahoo)."""

    model_config = ConfigDict(frozen=True)

    historical_prices_sec: int = Field(default=43200, ge=0)
    live_spot_price_sec: int = Field(default=60, ge=0)
    metadata_sec: int = Field(default=86400, ge=0)
    financial_statements_sec: int = Field(default=86400, ge=0)
    options_chain_sec: int = Field(default=300, ge=0)
    options_snapshot_sec: int = Field(default=300, ge=0)


class AlphaVantageTTLConfig(BaseModel):
    """TTLs for IHistoricalPriceProvider, IMetadataProvider (Alpha Vantage)."""

    model_config = ConfigDict(frozen=True)

    historical_prices_sec: int = Field(default=3600, ge=0)
    live_spot_price_sec: int = Field(default=60, ge=0)
    metadata_sec: int = Field(default=86400, ge=0)


class FredTTLConfig(BaseModel):
    """TTLs for IMacroProvider (FRED)."""

    model_config = ConfigDict(frozen=True)

    macro_series_sec: int = Field(default=86400, ge=0)


class MassiveTTLConfig(BaseModel):
    """TTLs for IOptionsProvider (Massive)."""

    model_config = ConfigDict(frozen=True)

    options_chain_sec: int = Field(default=300, ge=0)
    options_snapshot_sec: int = Field(default=300, ge=0)


class SentimentTTLConfig(BaseModel):
    """TTLs for IMarketIntelProvider (news / sentiment)."""

    model_config = ConfigDict(frozen=True)

    news_sec: int = Field(default=300, ge=0)
    sentiment_score_sec: int = Field(default=300, ge=0)


class TradingViewTTLConfig(BaseModel):
    """TTLs for IScreenerProvider (TradingView)."""

    model_config = ConfigDict(frozen=True)

    screener_sec: int = Field(default=300, ge=0)


class LlmTTLConfig(BaseModel):
    """TTLs for ILLMProvider (Groq, Gemini)."""

    model_config = ConfigDict(frozen=True)

    generate_response_sec: int = Field(default=3600, ge=0)


class HttpConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Per-provider; scraping sources override to curl_cffi.
    transport: Literal["curl_cffi", "httpx"] = "httpx"
    timeout_connect_sec: float = 10.0
    timeout_read_sec: float = 30.0
    impersonate: str | None = "chrome124"
    user_agent: str | None = None


class ScreenerSourceTTLConfig(BaseModel):
    """Per-source fetch TTL. ``None`` inherits ``ScreenerConfig.ttls.run_sec``."""

    model_config = ConfigDict(frozen=True)

    fetch_sec: int | None = Field(default=None, ge=0)


class ScreenerSourceConfig(BaseModel):
    """Rate limits and toggles for one screener source."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    ttls: ScreenerSourceTTLConfig = Field(default_factory=ScreenerSourceTTLConfig)
    default_limit: int | None = Field(default=None, ge=1)


class ScreenerSourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    yahoo_trending: ScreenerSourceConfig = Field(
        default_factory=lambda: ScreenerSourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=2.0),
            http=HttpConfig(transport="curl_cffi"),
        )
    )
    yahoo_predefined: ScreenerSourceConfig = Field(
        default_factory=lambda: ScreenerSourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=2.0),
            http=HttpConfig(transport="curl_cffi"),
            default_limit=50,
        )
    )
    finviz: ScreenerSourceConfig = Field(
        default_factory=lambda: ScreenerSourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=2.0),
            http=HttpConfig(transport="curl_cffi"),
        )
    )
    tradingview: ScreenerSourceConfig = Field(
        default_factory=lambda: ScreenerSourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=1.0),
            http=HttpConfig(transport="curl_cffi"),
        )
    )


class ScreenerTTLConfig(BaseModel):
    """TTLs for screener capability methods."""

    model_config = ConfigDict(frozen=True)

    run_sec: int = Field(default=300, ge=0)


class SentimentSourceTTLConfig(BaseModel):
    """Per-source fetch TTL. ``None`` inherits the method-level default on ``SentimentConfig``."""

    model_config = ConfigDict(frozen=True)

    fetch_sec: int | None = Field(default=None, ge=0)


class SentimentSourceConfig(BaseModel):
    """Rate limits and toggles for one intel source (Google News, StockTwits, Reddit, …)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    ttls: SentimentSourceTTLConfig = Field(default_factory=SentimentSourceTTLConfig)


class SentimentSourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    google_news: SentimentSourceConfig = Field(
        default_factory=lambda: SentimentSourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=1.0),
            http=HttpConfig(transport="curl_cffi"),
        )
    )
    stocktwits: SentimentSourceConfig = Field(
        default_factory=lambda: SentimentSourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=2.0),
        )
    )
    reddit: SentimentSourceConfig = Field(
        default_factory=lambda: SentimentSourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=0.5, max_retries=5),
            http=HttpConfig(transport="curl_cffi", user_agent="finpipe-scraper/1.0"),
        )
    )


class AbstractProviderConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)

    def ensure_configured(self) -> None:
        """Lazy validation hook for provider-specific requirements."""


class YahooConfig(AbstractProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=2.0)
    )
    ttls: YahooTTLConfig = Field(default_factory=YahooTTLConfig)


class FredConfig(AbstractProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=2.0)
    )
    ttls: FredTTLConfig = Field(default_factory=FredTTLConfig)
    api_key: str | None = Field(default_factory=lambda: os.getenv("FRED_API_KEY"))

    def ensure_configured(self) -> None:
        if not self.api_key:
            raise FinpipeConfigError("Missing required API key configuration: FRED_API_KEY")


class AlphaVantageConfig(AbstractProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=1.0)
    )
    ttls: AlphaVantageTTLConfig = Field(default_factory=AlphaVantageTTLConfig)
    api_key: str | None = Field(default_factory=lambda: os.getenv("ALPHA_VANTAGE_API_KEY"))

    def ensure_configured(self) -> None:
        if not self.api_key:
            raise FinpipeConfigError(
                "Missing required API key configuration: ALPHA_VANTAGE_API_KEY"
            )


class GroqConfig(AbstractProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(
            max_requests_per_second=10.0,
            max_requests_per_minute=30,
            max_tokens_per_minute=30_000,
        )
    )
    ttls: LlmTTLConfig = Field(default_factory=LlmTTLConfig)
    model: str = Field(
        default=DEFAULT_GROQ_MODEL,
        description="Default Groq chat model when generate_response is called without model=",
    )
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1)
    use_dynamic_model: bool = Field(
        default=False,
        description=(
            "When true, resolve the newest Groq Llama 70B model via the models API "
            "instead of using the configured model name"
        ),
    )
    api_key: str | None = Field(default_factory=lambda: os.getenv("GROQ_API_KEY"))

    def ensure_configured(self) -> None:
        if not self.api_key:
            raise FinpipeConfigError("Missing required API key configuration: GROQ_API_KEY")


class GeminiConfig(AbstractProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(
            max_requests_per_second=10.0,
            max_requests_per_minute=15,
            max_tokens_per_minute=250_000,
        )
    )
    ttls: LlmTTLConfig = Field(default_factory=LlmTTLConfig)
    model: str = Field(
        default=DEFAULT_GEMINI_MODEL,
        description="Default Gemini model when generate_response is called without model=",
    )
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1)
    api_key: str | None = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))

    def ensure_configured(self) -> None:
        if not self.api_key:
            raise FinpipeConfigError("Missing required API key configuration: GEMINI_API_KEY")


class MassiveConfig(AbstractProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=5.0)
    )
    ttls: MassiveTTLConfig = Field(default_factory=MassiveTTLConfig)
    api_key: str | None = Field(default_factory=lambda: os.getenv("MASSIVE_API_KEY"))
    access_key_id: str | None = Field(default_factory=lambda: os.getenv("MASSIVE_ACCESS_KEY_ID"))
    secret_access_key: str | None = Field(
        default_factory=lambda: os.getenv("MASSIVE_SECRET_ACCESS_KEY")
    )
    s3_endpoint: str | None = Field(default_factory=lambda: os.getenv("MASSIVE_S3_ENDPOINT"))
    s3_bucket: str | None = Field(default_factory=lambda: os.getenv("MASSIVE_S3_BUCKET"))

    def ensure_configured(self) -> None:
        missing = [
            k
            for k in [
                "api_key",
                "access_key_id",
                "secret_access_key",
                "s3_endpoint",
                "s3_bucket",
            ]
            if getattr(self, k) is None
        ]
        if missing:
            raise FinpipeConfigError(f"Missing required Massive configuration: {missing}")


class SentimentConfig(AbstractProviderConfig):
    """Market intel adapter; per-source limits live under ``sources``."""

    ttls: SentimentTTLConfig = Field(default_factory=SentimentTTLConfig)
    sources: SentimentSourcesConfig = Field(default_factory=SentimentSourcesConfig)

    def resolve_source_fetch_ttl(self, source_name: str) -> int:
        """Return per-source TTL, or inherit from ``news_sec`` / ``sentiment_score_sec``."""
        source = getattr(self.sources, source_name)
        if source.ttls.fetch_sec is not None:
            return source.ttls.fetch_sec
        if source_name == "google_news":
            return self.ttls.news_sec
        return self.ttls.sentiment_score_sec


class ScreenerConfig(AbstractProviderConfig):
    """Unified screener adapter; per-source limits live under ``sources``."""

    ttls: ScreenerTTLConfig = Field(default_factory=ScreenerTTLConfig)
    sources: ScreenerSourcesConfig = Field(default_factory=ScreenerSourcesConfig)

    def resolve_source_fetch_ttl(
        self,
        source_name: str,
        *,
        legacy_tradingview: TradingViewTTLConfig | None = None,
    ) -> int:
        """Return per-source TTL, or inherit from ``run_sec`` / legacy TradingView."""
        source = getattr(self.sources, source_name)
        if source.ttls.fetch_sec is not None:
            return source.ttls.fetch_sec
        if source_name == "tradingview" and legacy_tradingview is not None:
            return legacy_tradingview.screener_sec
        return self.ttls.run_sec


def resolve_screener_tradingview_source(
    screener: ScreenerConfig,
    legacy: TradingViewConfig,
) -> ScreenerSourceConfig:
    """Merge legacy ``providers.tradingview`` into ``screener.sources.tradingview``."""
    source = screener.sources.tradingview
    rate_limits = RateLimitConfig(
        **{**legacy.rate_limits.model_dump(), **source.rate_limits.model_dump()}
    )
    http = HttpConfig(**{**legacy.http.model_dump(), **source.http.model_dump()})
    fetch_sec = source.ttls.fetch_sec
    if fetch_sec is None:
        fetch_sec = legacy.ttls.screener_sec
    return ScreenerSourceConfig(
        enabled=source.enabled,
        rate_limits=rate_limits,
        http=http,
        ttls=ScreenerSourceTTLConfig(fetch_sec=fetch_sec),
        default_limit=source.default_limit,
    )


class TradingViewConfig(AbstractProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=1.0)
    )
    ttls: TradingViewTTLConfig = Field(default_factory=TradingViewTTLConfig)
    http: HttpConfig = Field(default_factory=lambda: HttpConfig(transport="curl_cffi"))


class ProviderGroupConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    fred: FredConfig = Field(default_factory=FredConfig)
    alpha_vantage: AlphaVantageConfig = Field(default_factory=AlphaVantageConfig)
    yahoo: YahooConfig = Field(default_factory=YahooConfig)
    sentiment: SentimentConfig = Field(default_factory=SentimentConfig)
    screener: ScreenerConfig = Field(default_factory=ScreenerConfig)
    massive: MassiveConfig = Field(default_factory=MassiveConfig)
    groq: GroqConfig = Field(default_factory=GroqConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    tradingview: TradingViewConfig = Field(default_factory=TradingViewConfig)


class RoutingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    equity_primary: str = "yahoo"
    equity_fallback: str | None = "alpha_vantage"
    options_primary: str = "massive"
    options_fallback: str | None = "yahoo"
    llm_primary: str = "groq"
    llm_fallback: str | None = "gemini"


class CacheConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    cache_type: Literal["memory", "sqlite", "none"] = "memory"
    sqlite_db_path: str = "finpipe_cache.db"
    sqlite_path: str | None = None
    maxsize: int = 1024
    namespace: str = "default"
    singleton: bool = True


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _settings_discovery_paths() -> list[Path]:
    paths: list[Path] = [
        Path("finpipe.settings.json"),
        Path(".finpipe") / "settings.json",
    ]
    if config_home := os.getenv("XDG_CONFIG_HOME"):
        paths.append(Path(config_home) / "finpipe" / "settings.json")
    else:
        paths.append(Path.home() / ".config" / "finpipe" / "settings.json")
    if appdata := os.getenv("APPDATA"):
        paths.append(Path(appdata) / "finpipe" / "settings.json")
    return paths


class FinpipeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataframe_format: Literal["polars", "pandas"] = "polars"
    providers: ProviderGroupConfig = Field(default_factory=ProviderGroupConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)

    @model_validator(mode="before")
    @classmethod
    def apply_env_overrides(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if backend := os.getenv("FINPIPE_CACHE_BACKEND"):
            cache = dict(data.get("cache") or {})
            cache["cache_type"] = backend
            data = {**data, "cache": cache}
        return data

    @property
    def massive(self) -> MassiveConfig:
        return self.providers.massive

    def get_required_key(self, key: str) -> str:
        mapping = _REQUIRED_KEYS.get(key)
        if mapping is None:
            raise FinpipeConfigError(f"Unknown required key: {key}")
        env_name, _provider = mapping
        value = os.getenv(env_name)
        if not value:
            raise FinpipeConfigError(f"Missing required configuration: {env_name}")
        return value

    @classmethod
    def from_env(cls) -> FinpipeConfig:
        return cls()

    @classmethod
    def load(cls, *, path: str | Path | None = None) -> FinpipeConfig:
        explicit = path or os.getenv("FINPIPE_CONFIG")
        if explicit:
            return cls.from_file(explicit)
        for candidate in _settings_discovery_paths():
            if candidate.is_file():
                return cls.from_file(candidate)
        return cls.from_env()

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        local_path: str | Path | None = None,
    ) -> FinpipeConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if local_path and Path(local_path).is_file():
            local_data = json.loads(Path(local_path).read_text(encoding="utf-8"))
            data = _deep_merge(data, local_data)
        base = cls.from_env().model_dump()
        return cls.model_validate(_deep_merge(base, data))

    @classmethod
    def from_json(cls, filepath: str) -> FinpipeConfig:
        return cls.from_file(filepath)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FinpipeConfig:
        base = cls.from_env().model_dump()
        return cls.model_validate(_deep_merge(base, data))

    def to_dict(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        """Return the resolved configuration as a plain dictionary."""
        from finpipe.core.settings_dump import dump_settings

        return dump_settings(self, redact_secrets=redact_secrets)

    def dump_settings(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        """Return settings for all capability facades and provider adapters."""
        return self.to_dict(redact_secrets=redact_secrets)

    def dump_settings_json(self, *, indent: int = 2, redact_secrets: bool = True) -> str:
        """Serialize all resolved settings to JSON."""
        from finpipe.core.settings_dump import dump_settings_json

        return dump_settings_json(self, indent=indent, redact_secrets=redact_secrets)
