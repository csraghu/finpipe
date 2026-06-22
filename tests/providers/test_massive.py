from datetime import date

import httpx
import pytest
import respx
from finpipe.providers.massive import MassiveOptionsAdapter


@pytest.mark.asyncio
async def test_massive_options_chain(config):
    adapter = MassiveOptionsAdapter(config)

    json_mock = {
        "data": {
            "expiration_date": "2023-01-15",
            "calls": [
                {
                    "contract_symbol": "AAPL230115C00150000",
                    "strike": 150.0,
                    "last_price": 5.0,
                    "in_the_money": False,
                }
            ],
            "puts": [
                {
                    "contract_symbol": "AAPL230115P00150000",
                    "strike": 150.0,
                    "last_price": 4.5,
                    "in_the_money": True,
                }
            ],
        }
    }

    with respx.mock:
        respx.get("https://api.massive.com/v1/options/chain").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        chain = await adapter.get_options_chain("AAPL", date(2023, 1, 15))
        assert chain.symbol == "AAPL"
        assert len(chain.calls) == 1


@pytest.mark.asyncio
async def test_massive_options_snapshot(config, pandas_config):
    adapter = MassiveOptionsAdapter(pandas_config)

    json_mock = {
        "data": [
            {"contract_symbol": "AAPL230115C00150000", "strike": 150.0, "last_price": 5.0},
            {"contract_symbol": "AAPL230115C00155000", "strike": 155.0, "last_price": 2.0},
        ]
    }

    with respx.mock:
        respx.get("https://api.massive.com/v1/options/snapshot").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        df = await adapter.get_options_snapshot("AAPL")
        assert len(df) == 2


@pytest.mark.asyncio
async def test_massive_empty_response(config, pandas_config):
    adapter = MassiveOptionsAdapter(pandas_config)

    with respx.mock:
        respx.get("https://api.massive.com/v1/options/snapshot").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        df = await adapter.get_options_snapshot("AAPL")
        assert len(df) == 0


@pytest.mark.asyncio
async def test_massive_v3_fetch_options_contracts(config):
    adapter = MassiveOptionsAdapter(config)

    with respx.mock:
        respx.get("https://api.massive.com/v3/reference/options/contracts").mock(
            return_value=httpx.Response(
                200,
                json={"results": [{"ticker": "O:AAPL230115C00150000"}]},
            )
        )
        contracts = await adapter.fetch_options_contracts("AAPL")
        assert len(contracts) == 1
        assert contracts[0]["ticker"] == "O:AAPL230115C00150000"


@pytest.mark.asyncio
async def test_massive_v3_fetch_options_snapshot(config):
    adapter = MassiveOptionsAdapter(config)

    with respx.mock:
        respx.get("https://api.massive.com/v3/snapshot/options/AAPL").mock(
            return_value=httpx.Response(
                200,
                json={"results": [{"details": {"ticker": "O:AAPL230115C00150000"}}]},
            )
        )
        snapshots = await adapter.fetch_options_snapshot("AAPL", limit=5)
        assert len(snapshots) == 1
