from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from finpipe.core.models import OptionChain, OptionContract
from finpipe.providers.composite import CompositeEquityService
from finpipe.providers.yahoo import YahooFinanceAdapter


@pytest.mark.asyncio
async def test_yahoo_fetch_options_contracts(config, mocker):
    adapter = YahooFinanceAdapter(config)
    mock_ticker = mocker.MagicMock()
    mock_ticker.options = ("2026-01-15",)
    mocker.patch("finpipe.providers.yahoo.yf.Ticker", return_value=mock_ticker)

    chain = OptionChain(
        symbol="AAPL",
        expiration_date=date(2026, 1, 15),
        calls=[OptionContract(contract_symbol="C1", strike=100.0, in_the_money=False)],
        puts=[OptionContract(contract_symbol="P1", strike=100.0, in_the_money=True)],
    )
    mocker.patch.object(adapter, "get_options_chain", AsyncMock(return_value=chain))

    contracts = await adapter.fetch_options_contracts("AAPL")
    assert len(contracts) == 2
    assert contracts[0]["contract_type"] == "call"


@pytest.mark.asyncio
async def test_yahoo_fetch_options_snapshot_filters(config, mocker):
    adapter = YahooFinanceAdapter(config)
    chain = OptionChain(
        symbol="AAPL",
        expiration_date=date(2026, 1, 15),
        calls=[
            OptionContract(
                contract_symbol="C1",
                strike=100.0,
                last_price=1.0,
                volume=10,
                in_the_money=False,
            ),
            OptionContract(
                contract_symbol="C2",
                strike=200.0,
                last_price=2.0,
                volume=20,
                in_the_money=False,
            ),
        ],
        puts=[],
    )
    mocker.patch.object(adapter, "get_options_chain", AsyncMock(return_value=chain))
    rows = await adapter.fetch_options_snapshot(
        "AAPL",
        expiration_date="2026-01-15",
        contract_type="call",
        strike_price_gte=150.0,
        limit=1,
    )
    assert len(rows) == 1
    assert rows[0]["details"]["ticker"] == "C2"


@pytest.mark.asyncio
async def test_yahoo_fetch_single_option_snapshot(config, mocker):
    adapter = YahooFinanceAdapter(config)
    mocker.patch.object(
        adapter,
        "fetch_options_snapshot",
        AsyncMock(
            return_value=[
                {"details": {"ticker": "O:AAPL260115C00100000"}},
            ]
        ),
    )
    found = await adapter.fetch_single_option_snapshot("AAPL", "AAPL260115C00100000")
    assert found["details"]["ticker"] == "O:AAPL260115C00100000"
    assert await adapter.fetch_single_option_snapshot("AAPL", "MISSING") == {}


@pytest.mark.asyncio
async def test_yahoo_massive_compat_stubs(config):
    adapter = YahooFinanceAdapter(config)
    assert await adapter.fetch_historical_aggs("O:AAPL", "2026-01-01", "2026-01-31") == []
    assert await adapter.sync_flatfile_from_s3("k", "/tmp/x") is False
    assert await adapter.list_s3_files("prefix") == []


@pytest.mark.asyncio
async def test_composite_equity_delegates_history_and_financials(config):
    yahoo = AsyncMock()
    yahoo.get_historical_prices.return_value = pd.DataFrame({"close": [1.0]})
    yahoo.get_financial_statements.return_value = {"income_statement": {}}
    equity = CompositeEquityService(
        config,
        adapters={"yahoo": yahoo, "alpha_vantage": AsyncMock()},
    )
    await equity.get_historical_prices("AAPL", date.today(), date.today())
    await equity.get_financial_statements("AAPL")
