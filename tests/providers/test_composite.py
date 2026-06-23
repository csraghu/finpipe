from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from finpipe.client import Client
from finpipe.core.config import FinpipeConfig
from finpipe.core.models import NewsArticle, OptionChain, TickerMetadata
from finpipe.providers.composite import (
    CompositeEquityService,
    CompositeOptionsService,
    call_with_fallback,
)


@pytest.mark.asyncio
async def test_call_with_fallback_uses_primary():
    primary = AsyncMock()
    primary.get_metadata.return_value = TickerMetadata(symbol="AAPL")
    fallback = AsyncMock()

    result = await call_with_fallback(
        {"yahoo": primary, "alpha_vantage": fallback},
        ["yahoo", "alpha_vantage"],
        "get_metadata",
        "AAPL",
    )

    assert result.symbol == "AAPL"
    primary.get_metadata.assert_awaited_once_with("AAPL")
    fallback.get_metadata.assert_not_called()


@pytest.mark.asyncio
async def test_call_with_fallback_falls_back_on_failure():
    primary = AsyncMock()
    primary.get_metadata.side_effect = RuntimeError("primary down")
    fallback = AsyncMock()
    fallback.get_metadata.return_value = TickerMetadata(symbol="AAPL")

    result = await call_with_fallback(
        {"yahoo": primary, "alpha_vantage": fallback},
        ["yahoo", "alpha_vantage"],
        "get_metadata",
        "AAPL",
    )

    assert result.symbol == "AAPL"
    fallback.get_metadata.assert_awaited_once_with("AAPL")


@pytest.mark.asyncio
async def test_composite_equity_routes_to_configured_primary(config):
    yahoo = AsyncMock()
    yahoo.get_live_spot_price.return_value = 123.45
    alpha = AsyncMock()

    equity = CompositeEquityService(
        config,
        adapters={"yahoo": yahoo, "alpha_vantage": alpha},
    )
    price = await equity.get_live_spot_price("AAPL")

    assert price == 123.45
    yahoo.get_live_spot_price.assert_awaited_once_with("AAPL")
    alpha.get_live_spot_price.assert_not_called()


@pytest.mark.asyncio
async def test_composite_equity_respects_fallback_routing():
    cfg = FinpipeConfig.from_dict(
        {"routing": {"equity_primary": "alpha_vantage", "equity_fallback": "yahoo"}}
    )
    yahoo = AsyncMock()
    yahoo.get_metadata.return_value = TickerMetadata(symbol="MSFT")
    alpha = AsyncMock()
    alpha.get_metadata.side_effect = RuntimeError("down")

    equity = CompositeEquityService(
        cfg,
        adapters={"yahoo": yahoo, "alpha_vantage": alpha},
    )
    meta = await equity.get_metadata("MSFT")

    assert meta.symbol == "MSFT"
    yahoo.get_metadata.assert_awaited_once_with("MSFT")


@pytest.mark.asyncio
async def test_composite_options_exposes_primary_api_key(config):
    massive = MagicMock()
    massive.api_key = "massive-key"
    yahoo = MagicMock()
    yahoo.api_key = None

    options = CompositeOptionsService(
        config,
        adapters={"massive": massive, "yahoo": yahoo},
    )
    assert options.api_key == "massive-key"


@pytest.mark.asyncio
async def test_composite_options_fetch_contracts_fallback():
    cfg = FinpipeConfig.from_dict(
        {"routing": {"options_primary": "massive", "options_fallback": "yahoo"}}
    )
    massive = AsyncMock()
    massive.fetch_options_contracts.side_effect = RuntimeError("massive down")
    yahoo = AsyncMock()
    yahoo.fetch_options_contracts.return_value = [{"ticker": "O:TEST"}]

    options = CompositeOptionsService(
        cfg,
        adapters={"massive": massive, "yahoo": yahoo},
    )
    rows = await options.fetch_options_contracts("AAPL")

    assert rows == [{"ticker": "O:TEST"}]
    yahoo.fetch_options_contracts.assert_awaited_once_with("AAPL")


@pytest.mark.asyncio
async def test_composite_equity_delegates_options_chain_to_options_service(config):
    yahoo = AsyncMock()
    options = AsyncMock()
    chain = OptionChain(symbol="AAPL", expiration_date=date.today())
    options.get_options_chain.return_value = chain

    equity = CompositeEquityService(
        config,
        adapters={"yahoo": yahoo, "alpha_vantage": AsyncMock()},
        options=options,
    )
    result = await equity.get_options_chain("AAPL")

    assert result.symbol == "AAPL"
    options.get_options_chain.assert_awaited_once_with("AAPL", None)


@pytest.mark.asyncio
async def test_client_intel_get_news(config):
    async with Client(config) as client:
        client.sentiment.get_news = AsyncMock(
            return_value=[
                NewsArticle(
                    title="Headline",
                    link="https://example.com",
                    published_at=datetime.now(),
                )
            ]
        )
        articles = await client.intel.get_news("AAPL", limit=5)

    assert len(articles) == 1
    assert articles[0].title == "Headline"
