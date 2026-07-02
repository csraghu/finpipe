"""finpipe v2 configuration.

Kept from v1 (review found these sound): frozen Pydantic models, settings-file
discovery, deep merge, env overrides, per-provider typed TTL blocks.

Changed per the rearchitecture plan:
- every credential is ``SecretStr`` (defense layer 1; see core/redact.py)
- NO eager validation: nothing here raises for a missing API key — adapters
  validate on first use (``ProviderAdapter._ensure_configured``)
- ``FinpipeConfig`` is never handed to adapters (see providers/base.py)
- Schwab omitted (out of scope per decision); legacy ``providers.tradingview``
  block dropped — TradingView is a screener source (``providers.screener.sources``)
- ``CacheConfig.strict`` added: cache serialization failures raise (tests/dev)
- default SQLite paths live OUTSIDE the project tree (runtime/paths.py)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

DEFAULT_GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
DEFAULT_NVIDIA_MODEL = "meta/llama-3.1-70b-instruct"

DataFrameFormat = Literal["polars", "pandas"]


def _env_secret(name: str) -> SecretStr | None:
    value = os.getenv(name)
    return SecretStr(value) if value else None


# --------------------------------------------------------------------------- shared
class RateLimitConfig(BaseModel):
    """User-tunable hard limits and resilience. AIMD tuning is internal."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_requests_per_second: float = Field(default=5.0, gt=0)
    max_requests_per_minute: int | None = Field(default=None, ge=1)
    max_tokens_per_minute: int | None = Field(default=None, ge=1)
    max_retries: int = 3
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout_sec: float = 60.0


class HttpConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    transport: Literal["curl_cffi", "httpx"] = "httpx"
    timeout_connect_sec: float = 10.0
    timeout_read_sec: float = 30.0
    impersonate: str | None = "chrome124"  # curl_cffi only
    user_agent: str | None = None


# --------------------------------------------------------------------------- TTLs
class YahooTTLConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    historical_prices_sec: int = Field(default=43200, ge=0)
    live_spot_price_sec: int = Field(default=0, ge=0)  # 0 = always refetch (still stored)
    metadata_sec: int = Field(default=86400, ge=0)
    financial_statements_sec: int = Field(default=86400, ge=0)
    options_chain_sec: int = Field(default=300, ge=0)
    options_snapshot_sec: int = Field(default=300, ge=0)


class AlphaVantageTTLConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    historical_prices_sec: int = Field(default=3600, ge=0)
    live_spot_price_sec: int = Field(default=60, ge=0)
    metadata_sec: int = Field(default=86400, ge=0)


class FredTTLConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    macro_series_sec: int = Field(default=86400, ge=0)


class MassiveTTLConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    options_chain_sec: int = Field(default=300, ge=0)
    options_snapshot_sec: int = Field(default=300, ge=0)


class SentimentTTLConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    news_sec: int = Field(default=300, ge=0)
    sentiment_score_sec: int = Field(default=300, ge=0)


class ScreenerTTLConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_sec: int = Field(default=300, ge=0)


class LlmTTLConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    generate_response_sec: int = Field(default=3600, ge=0)


# --------------------------------------------------------------------------- sources
class SourceTTLConfig(BaseModel):
    """Per-source fetch TTL; ``None`` inherits the provider-level default."""

    model_config = ConfigDict(frozen=True)

    fetch_sec: int | None = Field(default=None, ge=0)


class SourceConfig(BaseModel):
    """Rate limits and toggles for one sub-source (intel or screener)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    ttls: SourceTTLConfig = Field(default_factory=SourceTTLConfig)
    default_limit: int | None = Field(default=None, ge=1)


class RedditSourceConfig(SourceConfig):
    client_id: SecretStr | None = Field(default_factory=lambda: _env_secret("REDDIT_CLIENT_ID"))
    client_secret: SecretStr | None = Field(default_factory=lambda: _env_secret("REDDIT_CLIENT_SECRET"))


class SentimentSourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    google_news: SourceConfig = Field(
        default_factory=lambda: SourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=1.0),
            http=HttpConfig(transport="curl_cffi"),
        )
    )
    stocktwits: SourceConfig = Field(
        default_factory=lambda: SourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=1.0),
            http=HttpConfig(transport="curl_cffi"),
        )
    )
    reddit: RedditSourceConfig = Field(
        default_factory=lambda: RedditSourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=0.5, max_retries=2),
            http=HttpConfig(transport="httpx", user_agent="python:finpipe:v2"),
        )
    )


class ScreenerSourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    yahoo_trending: SourceConfig = Field(
        default_factory=lambda: SourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=2.0),
            http=HttpConfig(transport="curl_cffi"),
        )
    )
    yahoo_predefined: SourceConfig = Field(
        default_factory=lambda: SourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=2.0),
            http=HttpConfig(transport="curl_cffi"),
            default_limit=50,
        )
    )
    finviz: SourceConfig = Field(
        default_factory=lambda: SourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=2.0),
            http=HttpConfig(
                transport="httpx",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            ),
        )
    )
    tradingview: SourceConfig = Field(
        default_factory=lambda: SourceConfig(
            rate_limits=RateLimitConfig(max_requests_per_second=1.0),
            http=HttpConfig(transport="curl_cffi"),
        )
    )


# --------------------------------------------------------------------------- providers
class YahooConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=2.0)
    )
    ttls: YahooTTLConfig = Field(default_factory=YahooTTLConfig)


class AlphaVantageConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=0.083)
    )
    http: HttpConfig = Field(default_factory=HttpConfig)
    ttls: AlphaVantageTTLConfig = Field(default_factory=AlphaVantageTTLConfig)
    api_key: SecretStr | None = Field(default_factory=lambda: _env_secret("ALPHA_VANTAGE_API_KEY"))


class FredConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=2.0)
    )
    http: HttpConfig = Field(default_factory=HttpConfig)
    ttls: FredTTLConfig = Field(default_factory=FredTTLConfig)
    api_key: SecretStr | None = Field(default_factory=lambda: _env_secret("FRED_API_KEY"))


class MassiveConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=5.0)
    )
    http: HttpConfig = Field(default_factory=HttpConfig)
    ttls: MassiveTTLConfig = Field(default_factory=MassiveTTLConfig)
    api_key: SecretStr | None = Field(default_factory=lambda: _env_secret("MASSIVE_API_KEY"))
    access_key_id: SecretStr | None = Field(default_factory=lambda: _env_secret("MASSIVE_ACCESS_KEY_ID"))
    secret_access_key: SecretStr | None = Field(
        default_factory=lambda: _env_secret("MASSIVE_SECRET_ACCESS_KEY")
    )
    s3_endpoint: str | None = Field(default_factory=lambda: os.getenv("MASSIVE_S3_ENDPOINT"))
    s3_bucket: str | None = Field(default_factory=lambda: os.getenv("MASSIVE_S3_BUCKET"))


class SentimentConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    ttls: SentimentTTLConfig = Field(default_factory=SentimentTTLConfig)
    sources: SentimentSourcesConfig = Field(default_factory=SentimentSourcesConfig)

    def source_fetch_ttl(self, source_name: str) -> int:
        source: SourceConfig = getattr(self.sources, source_name)
        if source.ttls.fetch_sec is not None:
            return source.ttls.fetch_sec
        return self.ttls.news_sec if source_name == "google_news" else self.ttls.sentiment_score_sec


class ScreenerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    ttls: ScreenerTTLConfig = Field(default_factory=ScreenerTTLConfig)
    sources: ScreenerSourcesConfig = Field(default_factory=ScreenerSourcesConfig)

    def source_fetch_ttl(self, source_name: str) -> int:
        source: SourceConfig = getattr(self.sources, source_name)
        return source.ttls.fetch_sec if source.ttls.fetch_sec is not None else self.ttls.run_sec


class LlmProviderConfig(BaseModel):
    """Shared shape for Groq / Gemini / NVIDIA blocks."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    ttls: LlmTTLConfig = Field(default_factory=LlmTTLConfig)
    model: str = ""
    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1)
    api_key: SecretStr | None = None


class GroqConfig(LlmProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(
            max_requests_per_second=10.0, max_requests_per_minute=30, max_tokens_per_minute=30_000
        )
    )
    model: str = DEFAULT_GROQ_MODEL
    api_key: SecretStr | None = Field(default_factory=lambda: _env_secret("GROQ_API_KEY"))


class GeminiConfig(LlmProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(
            max_requests_per_second=10.0, max_requests_per_minute=15, max_tokens_per_minute=250_000
        )
    )
    model: str = DEFAULT_GEMINI_MODEL
    api_key: SecretStr | None = Field(default_factory=lambda: _env_secret("GEMINI_API_KEY"))


class NvidiaConfig(LlmProviderConfig):
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(
            max_requests_per_second=10.0, max_requests_per_minute=30, max_tokens_per_minute=30_000
        )
    )
    model: str = DEFAULT_NVIDIA_MODEL
    api_key: SecretStr | None = Field(default_factory=lambda: _env_secret("NVIDIA_API_KEY"))


class LlmPromptCompressionConfig(BaseModel):
    """Remote LLMLingua compression for all LLM adapters (optional)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    target_ratio: float = Field(default=0.5, gt=0.0, le=1.0)
    min_chars: int = Field(default=400, ge=0)
    endpoint_url: str | None = None
    model_name: str = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"
    rate_limits: RateLimitConfig = Field(
        default_factory=lambda: RateLimitConfig(max_requests_per_second=0.2, max_retries=3)
    )


class LlmPromptConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    compression: LlmPromptCompressionConfig = Field(default_factory=LlmPromptCompressionConfig)


class ProviderGroupConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    yahoo: YahooConfig = Field(default_factory=YahooConfig)
    alpha_vantage: AlphaVantageConfig = Field(default_factory=AlphaVantageConfig)
    fred: FredConfig = Field(default_factory=FredConfig)
    massive: MassiveConfig = Field(default_factory=MassiveConfig)
    sentiment: SentimentConfig = Field(default_factory=SentimentConfig)
    screener: ScreenerConfig = Field(default_factory=ScreenerConfig)
    groq: GroqConfig = Field(default_factory=GroqConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    nvidia: NvidiaConfig = Field(default_factory=NvidiaConfig)


class RoutingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    equity_primary: str = "yahoo"
    equity_fallback: str | None = "alpha_vantage"
    options_primary: str = "massive"
    options_fallback: str | None = "yahoo"
    llm_primary: str = "groq"
    llm_fallback: str | None = "gemini"


class HealthConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    probe_symbol: str = Field(default="AAPL", min_length=1)


class CacheConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    cache_type: Literal["memory", "sqlite", "none"] = "memory"
    sqlite_path: str | None = None  # None → runtime/paths.py default (outside synced tree)
    maxsize: int = 1024
    namespace: str = "default"
    singleton: bool = True
    strict: bool = False  # True: unserializable cache values raise (recommended in tests)


# --------------------------------------------------------------------------- loader
def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _settings_discovery_paths() -> list[Path]:
    paths = [Path("finpipe.settings.json"), Path(".finpipe") / "settings.json"]
    if config_home := os.getenv("XDG_CONFIG_HOME"):
        paths.append(Path(config_home) / "finpipe" / "settings.json")
    else:
        paths.append(Path.home() / ".config" / "finpipe" / "settings.json")
    if appdata := os.getenv("APPDATA"):
        paths.append(Path(appdata) / "finpipe" / "settings.json")
    return paths


class FinpipeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    dataframe_format: DataFrameFormat = "polars"
    providers: ProviderGroupConfig = Field(default_factory=ProviderGroupConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    health: HealthConfig = Field(default_factory=HealthConfig)
    llm_prompt: LlmPromptConfig = Field(default_factory=LlmPromptConfig)

    @model_validator(mode="before")
    @classmethod
    def _apply_env_overrides(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if backend := os.getenv("FINPIPE_CACHE_BACKEND"):
            cache = dict(data.get("cache") or {})
            cache["cache_type"] = backend
            data = {**data, "cache": cache}
        return data

    @classmethod
    def load(cls, *, path: str | Path | None = None) -> FinpipeConfig:
        explicit = path or os.getenv("FINPIPE_CONFIG")
        if explicit:
            return cls.from_file(explicit)
        for candidate in _settings_discovery_paths():
            if candidate.is_file():
                return cls.from_file(candidate)
        return cls()

    @classmethod
    def from_file(cls, path: str | Path, *, local_path: str | Path | None = None) -> FinpipeConfig:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if local_path and Path(local_path).is_file():
            data = _deep_merge(data, json.loads(Path(local_path).read_text(encoding="utf-8")))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FinpipeConfig:
        base = cls().model_dump(exclude_none=False)
        base = _unwrap_secrets(base)
        return cls.model_validate(_deep_merge(base, data))

    def dump_settings(self, *, redact_secrets: bool = True) -> dict[str, Any]:
        from ..observe.settings_dump import dump_settings

        return dump_settings(self, redact_secrets=redact_secrets)


def _unwrap_secrets(data: Any) -> Any:
    """model_dump() renders SecretStr as '**********'; unwrap for re-validation merges."""
    if isinstance(data, SecretStr):
        return data.get_secret_value()
    if isinstance(data, dict):
        return {k: _unwrap_secrets(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_unwrap_secrets(v) for v in data]
    return data
