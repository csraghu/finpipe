"""Config precedence + the secret-leak contract scan (review §3 guard)."""

from __future__ import annotations

import json

from finpipe.core.config import FinpipeConfig
from finpipe.core.redact import is_secret_field, redact
from pydantic import SecretStr


def test_from_dict_deep_merges_over_defaults():
    config = FinpipeConfig.from_dict(
        {"providers": {"fred": {"rate_limits": {"max_requests_per_second": 1.5}}}}
    )
    assert config.providers.fred.rate_limits.max_requests_per_second == 1.5
    # untouched siblings keep defaults
    assert config.providers.fred.ttls.macro_series_sec == 86400
    assert config.providers.yahoo.rate_limits.max_requests_per_second == 2.0


def test_env_cache_backend_override(monkeypatch):
    monkeypatch.setenv("FINPIPE_CACHE_BACKEND", "sqlite")
    config = FinpipeConfig.from_dict({})
    assert config.cache.cache_type == "sqlite"


def test_secret_fields_are_secretstr(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fred-secret-value")
    config = FinpipeConfig.from_dict({})
    assert isinstance(config.providers.fred.api_key, SecretStr)
    assert "fred-secret-value" not in repr(config)


def test_redact_by_suffix_and_type():
    payload = {
        "api_key": "k",
        "client_secret": "s",
        "refresh_token": "t",
        "app_secret": "a",     # the exact field v1 leaked
        "harmless": "x",
        "nested": {"access_key_id": "id", "list": [{"my_password": "p"}]},
        "typed": SecretStr("v"),
    }
    cleaned = redact(payload)
    blob = json.dumps(cleaned)
    for secret in ("\"k\"", "\"s\"", "\"t\"", "\"a\"", "\"id\"", "\"p\"", "\"v\""):
        assert secret not in blob
    assert cleaned["harmless"] == "x"
    assert is_secret_field("SCHWAB_REFRESH_TOKEN".lower())


def test_settings_dump_leaks_no_configured_secret_values(monkeypatch):
    """Contract scan: every configured secret must be absent from the dump."""
    secrets = {
        "FRED_API_KEY": "fred-sec-123",
        "ALPHA_VANTAGE_API_KEY": "av-sec-456",
        "GROQ_API_KEY": "groq-sec-789",
        "GEMINI_API_KEY": "gem-sec-012",
        "NVIDIA_API_KEY": "nv-sec-345",
        "MASSIVE_API_KEY": "mas-sec-678",
        "MASSIVE_ACCESS_KEY_ID": "mas-id-901",
        "MASSIVE_SECRET_ACCESS_KEY": "mas-sak-234",
        "REDDIT_CLIENT_ID": "red-id-567",
        "REDDIT_CLIENT_SECRET": "red-sec-890",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    config = FinpipeConfig.from_dict({})
    from finpipe.observe.settings_dump import dump_settings_json

    blob = dump_settings_json(config, redact_secrets=True)
    for value in secrets.values():
        assert value not in blob, f"secret value leaked into dump: {value}"
