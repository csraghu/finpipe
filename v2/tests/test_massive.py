"""Massive options adapter tests: digest cache keys, honest taxonomy, empty chains."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from finpipe.core.config import MassiveConfig
from finpipe.core.errors import FinpipeConfigError
from finpipe.providers.massive import MassiveOptionsAdapter
from pydantic import SecretStr

from conftest import FakeExecutor, FakeResponse, make_runtime


def _adapter(executor: FakeExecutor, *, api_key: str | None = "mk") -> MassiveOptionsAdapter:
    config = MassiveConfig(api_key=SecretStr(api_key) if api_key else None)
    return MassiveOptionsAdapter(make_runtime(config, executor, provider_key="massive"))


async def test_missing_api_key_raises_config_error():
    """Missing API key raises on first use, not construction."""
    adapter = _adapter(FakeExecutor(), api_key=None)
    with pytest.raises(FinpipeConfigError, match="MASSIVE_API_KEY"):
        await adapter.fetch_options_contracts("AAPL")


async def test_options_chain_from_snapshot():
    """Options chain is parsed correctly from snapshot data."""
    payload = {
        "results": [
            {
                "details": {
                    "ticker": "O:AAPL260117C00150000",
                    "strike_price": 150.0,
                    "contract_type": "call",
                    "expiration_date": "2026-01-17",
                },
                "day": {"close": 10.5, "volume": 100},
                "last_quote": {"bid": 10.4, "ask": 10.6},
                "open_interest": 500,
                "implied_volatility": 0.2,
            },
            {
                "details": {
                    "ticker": "O:AAPL260117P00150000",
                    "strike_price": 150.0,
                    "contract_type": "put",
                    "expiration_date": "2026-01-17",
                },
                "day": {"close": 9.5, "volume": 80},
                "last_quote": {"bid": 9.4, "ask": 9.6},
                "open_interest": 400,
                "implied_volatility": 0.19,
            },
        ]
    }
    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)
    chain = await adapter.get_options_chain("AAPL")

    assert chain.symbol == "AAPL"
    assert chain.expiration_date == date(2026, 1, 17)
    assert len(chain.calls) == 1
    assert chain.calls[0].strike == 150.0
    assert len(chain.puts) == 1


async def test_empty_options_chain():
    """Empty results return an empty OptionChain."""
    payload = {"results": []}
    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)
    chain = await adapter.get_options_chain("BADTICKER")

    assert chain.symbol == "BADTICKER"
    assert len(chain.calls) == 0
    assert len(chain.puts) == 0


async def test_options_chain_caching():
    """Options chains are cached by symbol and expiration date."""
    payload = {
        "results": [
            {
                "details": {
                    "ticker": "O:AAPL260117C00150000",
                    "strike_price": 150.0,
                    "contract_type": "call",
                    "expiration_date": "2026-01-17",
                },
                "day": {"close": 10.5, "volume": 100},
                "last_quote": {"bid": 10.4, "ask": 10.6},
                "open_interest": 500,
                "implied_volatility": 0.2,
            }
        ]
    }
    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)

    exp = date(2026, 1, 17)
    first = await adapter.get_options_chain("AAPL", exp)
    second = await adapter.get_options_chain("AAPL", exp)

    assert len(executor.calls) == 1  # second call served from cache
    assert first.calls[0].strike == second.calls[0].strike


async def test_options_snapshot_flattened():
    """Snapshot rows are flattened for DataFrame output."""
    payload = {
        "results": [
            {
                "details": {
                    "ticker": "O:AAPL260117C00150000",
                    "strike_price": 150.0,
                    "contract_type": "call",
                    "expiration_date": "2026-01-17",
                },
                "day": {"close": 10.5, "volume": 100},
                "last_quote": {"bid": 10.4, "ask": 10.6},
                "open_interest": 500,
                "implied_volatility": 0.2,
            }
        ]
    }
    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)
    frame = await adapter.get_options_snapshot("AAPL")

    assert isinstance(frame, pl.DataFrame)
    assert frame.height == 1


async def test_fetch_options_contracts_raw_list():
    """fetch_options_contracts returns raw contract list."""
    payload = {
        "results": [
            {"ticker": "O:AAPL260117C00150000", "strike_price": 150.0},
            {"ticker": "O:AAPL260117P00150000", "strike_price": 150.0},
        ]
    }
    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor)
    contracts = await adapter.fetch_options_contracts("AAPL")

    assert len(contracts) == 2
    assert contracts[0]["ticker"] == "O:AAPL260117C00150000"


async def test_api_key_sent_as_param_never_in_url():
    """API key is sent as param, never in URL path."""
    payload = {"results": []}
    executor = FakeExecutor([FakeResponse(200, json_data=payload)])
    adapter = _adapter(executor, api_key="secret123")
    await adapter.fetch_options_contracts("AAPL")

    method, url, kwargs = executor.calls[0]
    assert "secret123" not in url
    assert kwargs["params"]["apiKey"] == "secret123"


async def test_malformed_json_response_returns_empty():
    """Malformed JSON responses return empty results gracefully."""
    executor = FakeExecutor([FakeResponse(200, json_data="not a dict")])
    adapter = _adapter(executor)
    contracts = await adapter.fetch_options_contracts("AAPL")

    assert contracts == []
