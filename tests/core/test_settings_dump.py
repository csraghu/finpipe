import json

from finpipe.client import Client
from finpipe.core.config import FinpipeConfig
from finpipe.core.settings_dump import PROVIDER_NAMES


def test_dump_settings_includes_all_providers(config):
    settings = config.dump_settings(redact_secrets=False)

    assert set(settings["providers"].keys()) == set(PROVIDER_NAMES)
    assert settings["providers"]["fred"]["api_key"] == "test_fred"
    assert settings["cache"]["cache_type"] == "memory"
    assert settings["routing"]["equity_primary"] == "yahoo"


def test_dump_settings_redacts_secrets(config):
    settings = config.dump_settings(redact_secrets=True)

    assert settings["providers"]["fred"]["api_key"] == "<redacted>"
    assert settings["providers"]["massive"]["secret_access_key"] == "<redacted>"
    assert settings["providers"]["yahoo"]["rate_limits"]["max_requests_per_second"] == 2.0
    assert "historical_prices_sec" in settings["providers"]["yahoo"]["ttls"]
    assert "macro_series_sec" in settings["providers"]["fred"]["ttls"]
    assert "news_sec" in settings["providers"]["sentiment"]["ttls"]
    assert "generate_response_sec" in settings["providers"]["groq"]["ttls"]
    assert "historical_prices_sec" not in settings["providers"]["fred"]["ttls"]


def test_dump_settings_capabilities(config):
    settings = config.dump_settings()
    equity = settings["capabilities"]["equity"]

    assert equity["primary"] == "yahoo"
    assert equity["fallback"] == "alpha_vantage"
    assert "IHistoricalPriceProvider" in equity["protocols"]
    assert set(equity["providers"].keys()) == {"yahoo", "alpha_vantage"}


def test_dump_settings_json_roundtrip(config):
    payload = json.loads(config.dump_settings_json())

    assert payload["dataframe_format"] == "polars"
    assert "llm" in payload["capabilities"]
    assert payload["capabilities"]["llm"]["primary"] == "groq"


def test_client_dump_settings(config):
    client = Client(config)
    settings = client.dump_settings()

    assert settings["providers"]["gemini"]["api_key"] == "<redacted>"
    assert settings["capabilities"]["screener"]["providers"]["tradingview"]["enabled"] is True
