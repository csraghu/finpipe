import pytest
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeConfigError


def test_finpipe_config_loads_env_vars(config):
    assert config.massive.api_key == "test_massive"
    assert config.get_required_key("fred_api_key") == "test_fred"
    assert config.cache.cache_type == "memory"


def test_get_required_key_raises_error(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    config = FinpipeConfig()
    with pytest.raises(FinpipeConfigError):
        config.get_required_key("fred_api_key")


def test_provider_specific_yahoo_ttls(config):
    ttls = config.providers.yahoo.ttls
    assert hasattr(ttls, "historical_prices_sec")
    assert hasattr(ttls, "financial_statements_sec")
    assert not hasattr(ttls, "macro_series_sec")
    assert not hasattr(ttls, "llm_sec")


def test_provider_specific_fred_ttls(config):
    ttls = config.providers.fred.ttls
    assert ttls.macro_series_sec == 86400
    assert not hasattr(ttls, "historical_prices_sec")


def test_provider_specific_sentiment_ttls(config):
    ttls = config.providers.sentiment.ttls
    assert ttls.news_sec == 300
    assert ttls.sentiment_score_sec == 300


def test_sentiment_per_source_rate_limits(config):
    sources = config.providers.sentiment.sources
    assert sources.google_news.rate_limits.max_requests_per_second == 1.0
    assert sources.stocktwits.rate_limits.max_requests_per_second == 2.0
    assert sources.reddit.rate_limits.max_requests_per_second == 0.5
    assert sources.reddit.rate_limits.max_retries == 1


def test_sentiment_scraping_sources_default_curl_cffi():
    sources = FinpipeConfig().providers.sentiment.sources
    assert sources.google_news.http.transport == "curl_cffi"
    assert sources.stocktwits.http.transport == "curl_cffi"
    assert sources.reddit.http.transport == "curl_cffi"
    assert sources.reddit.rate_limits.max_retries == 1


def test_sentiment_per_source_merge_from_dict():
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "sentiment": {
                    "sources": {
                        "reddit": {
                            "enabled": False,
                            "rate_limits": {"max_requests_per_second": 0.25},
                        },
                        "stocktwits": {
                            "rate_limits": {"max_requests_per_second": 5.0},
                        },
                    }
                }
            }
        }
    )
    assert config.providers.sentiment.sources.reddit.enabled is False
    assert config.providers.sentiment.sources.reddit.rate_limits.max_requests_per_second == 0.25
    assert config.providers.sentiment.sources.stocktwits.rate_limits.max_requests_per_second == 5.0
    assert config.providers.sentiment.sources.google_news.enabled is True


def test_sentiment_source_ttl_inherits_global(config):
    sentiment = config.providers.sentiment
    assert sentiment.resolve_source_fetch_ttl("google_news") == sentiment.ttls.news_sec
    assert sentiment.resolve_source_fetch_ttl("stocktwits") == sentiment.ttls.sentiment_score_sec
    assert sentiment.resolve_source_fetch_ttl("reddit") == sentiment.ttls.sentiment_score_sec


def test_sentiment_source_ttl_override():
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "sentiment": {
                    "ttls": {"news_sec": 300, "sentiment_score_sec": 300},
                    "sources": {
                        "reddit": {"ttls": {"fetch_sec": 7200}},
                        "stocktwits": {"ttls": {"fetch_sec": 60}},
                    },
                }
            }
        }
    )
    assert config.providers.sentiment.resolve_source_fetch_ttl("reddit") == 7200
    assert config.providers.sentiment.resolve_source_fetch_ttl("stocktwits") == 60
    assert config.providers.sentiment.resolve_source_fetch_ttl("google_news") == 300


def test_provider_ttls_merge_from_dict():
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "yahoo": {
                    "ttls": {
                        "live_spot_price_sec": 0,
                        "historical_prices_sec": 7200,
                    }
                },
                "fred": {"ttls": {"macro_series_sec": 3600}},
            }
        }
    )
    assert config.providers.yahoo.ttls.live_spot_price_sec == 0
    assert config.providers.yahoo.ttls.historical_prices_sec == 7200
    assert config.providers.yahoo.ttls.metadata_sec == 86400
    assert config.providers.fred.ttls.macro_series_sec == 3600


def test_llm_provider_default_models(config):
    assert config.providers.groq.model == "meta-llama/llama-4-scout-17b-16e-instruct"
    assert config.providers.gemini.model == "gemini-3.1-flash-lite"
    assert config.providers.nvidia.model == "meta/llama-3.1-70b-instruct"


def test_llm_provider_model_merge_from_dict():
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "groq": {"model": "llama-3.3-70b-versatile"},
                "gemini": {"model": "gemini-2.0-flash"},
            }
        }
    )
    assert config.providers.groq.model == "llama-3.3-70b-versatile"
    assert config.providers.gemini.model == "gemini-2.0-flash"


def test_llm_prompt_compression_defaults(config):
    assert config.llm_prompt.compression.enabled is True
    assert config.llm_prompt.compression.target_ratio == 0.5


def test_migrate_legacy_gemini_prompt_compression():
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "gemini": {
                    "prompt_compression": {
                        "enabled": False,
                        "target_ratio": 0.3,
                        "min_chars": 100,
                        "device": "cuda",
                    }
                }
            }
        }
    )
    assert config.llm_prompt.compression.enabled is False
    assert config.llm_prompt.compression.target_ratio == 0.3
    assert "prompt_compression" not in config.providers.gemini.model_dump()


def test_ensure_configured_noop_when_provider_disabled(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)

    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "nvidia": {"enabled": False},
                "groq": {"enabled": False},
                "gemini": {"enabled": False},
                "fred": {"enabled": False},
                "alpha_vantage": {"enabled": False},
            }
        }
    )
    config.providers.nvidia.ensure_configured()
    config.providers.groq.ensure_configured()
    config.providers.gemini.ensure_configured()
    config.providers.fred.ensure_configured()
    config.providers.alpha_vantage.ensure_configured()


def test_client_init_skips_disabled_provider_key_validation(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    monkeypatch.setenv("MASSIVE_API_KEY", "test_massive")
    monkeypatch.setenv("MASSIVE_ACCESS_KEY_ID", "test_id")
    monkeypatch.setenv("MASSIVE_SECRET_ACCESS_KEY", "test_secret")
    monkeypatch.setenv("MASSIVE_S3_ENDPOINT", "http://test")
    monkeypatch.setenv("MASSIVE_S3_BUCKET", "test_bucket")
    monkeypatch.setenv("FINPIPE_CACHE_BACKEND", "memory")

    from finpipe import Client

    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "nvidia": {"enabled": False},
                "groq": {"enabled": False},
                "gemini": {"enabled": False},
                "fred": {"enabled": False},
                "alpha_vantage": {"enabled": False},
            }
        }
    )
    Client(config)


def test_screener_per_source_rate_limits(config):
    sources = config.providers.screener.sources
    assert sources.yahoo_trending.rate_limits.max_requests_per_second == 2.0
    assert sources.yahoo_predefined.rate_limits.max_requests_per_second == 2.0
    assert sources.yahoo_predefined.default_limit == 50
    assert sources.finviz.rate_limits.max_requests_per_second == 2.0
    assert sources.tradingview.rate_limits.max_requests_per_second == 1.0


def test_screener_source_ttl_inherits_run_sec(config):
    screener = config.providers.screener
    assert screener.resolve_source_fetch_ttl("yahoo_trending") == screener.ttls.run_sec
    assert screener.resolve_source_fetch_ttl("finviz") == screener.ttls.run_sec


def test_screener_source_ttl_override():
    config = FinpipeConfig.from_dict(
        {
            "providers": {
                "screener": {
                    "ttls": {"run_sec": 300},
                    "sources": {
                        "finviz": {"ttls": {"fetch_sec": 600}},
                    },
                }
            }
        }
    )
    assert config.providers.screener.resolve_source_fetch_ttl("finviz") == 600
    assert config.providers.screener.resolve_source_fetch_ttl("yahoo_trending") == 300
