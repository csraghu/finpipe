from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pandas as pd
import polars as pl
import pytest
import respx
from finpipe.core.models import OptionChain
from finpipe.providers.massive import MassiveOptionsAdapter


@pytest.mark.asyncio
async def test_massive_fetch_options_snapshot_with_filters(config):
    adapter = MassiveOptionsAdapter(config)
    with respx.mock:
        route = respx.get("https://api.massive.com/v3/snapshot/options/AAPL").mock(
            return_value=httpx.Response(200, json={"results": [{"x": 1}]})
        )
        rows = await adapter.fetch_options_snapshot(
            "aapl",
            expiration_date="2026-01-01",
            contract_type="call",
            strike_price_gte=100.0,
            strike_price_lte=200.0,
            sort="strike",
            order="asc",
            limit=10,
        )
        assert rows == [{"x": 1}]
        assert "strike_price.gte" in str(route.calls[0].request.url)


@pytest.mark.asyncio
async def test_massive_fetch_single_option_snapshot_prefix(config):
    adapter = MassiveOptionsAdapter(config)
    with respx.mock:
        respx.get("https://api.massive.com/v3/snapshot/options/AAPL/O:AAPL260101C00150000").mock(
            return_value=httpx.Response(200, json={"results": {"ticker": "O:AAPL260101C00150000"}})
        )
        payload = await adapter.fetch_single_option_snapshot("AAPL", "AAPL260101C00150000")
        assert payload["ticker"] == "O:AAPL260101C00150000"


@pytest.mark.asyncio
async def test_massive_fetch_historical_aggs(config):
    adapter = MassiveOptionsAdapter(config)
    with respx.mock:
        respx.get(url__startswith="https://api.massive.com/v2/aggs/ticker/O:AAPL").mock(
            return_value=httpx.Response(200, json={"results": [{"c": 1.0}]})
        )
        rows = await adapter.fetch_historical_aggs("AAPL", "2026-01-01", "2026-01-31")
        assert rows == [{"c": 1.0}]


@pytest.mark.asyncio
async def test_massive_sync_flatfile_from_s3_success(config, tmp_path):
    adapter = MassiveOptionsAdapter(config)
    body = MagicMock()
    body.read = AsyncMock(return_value=b"flatfile")
    s3 = AsyncMock()
    s3.get_object = AsyncMock(return_value={"Body": body})
    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=s3)
    client_cm.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.client = MagicMock(return_value=client_cm)

    with patch.object(adapter, "_get_aioboto3_session", return_value=session):
        dest = tmp_path / "nested" / "file.csv"
        assert await adapter.sync_flatfile_from_s3("remote/key", str(dest)) is True
        assert dest.read_bytes() == b"flatfile"


@pytest.mark.asyncio
async def test_massive_sync_flatfile_missing_credentials(config, tmp_path):
    adapter = MassiveOptionsAdapter(config)
    with patch.object(adapter, "_get_aioboto3_session", return_value=None):
        assert await adapter.sync_flatfile_from_s3("k", str(tmp_path / "f.csv")) is False


@pytest.mark.asyncio
async def test_massive_sync_flatfile_not_found(config, tmp_path):
    from botocore.exceptions import ClientError

    adapter = MassiveOptionsAdapter(config)
    s3 = AsyncMock()
    s3.get_object = AsyncMock(side_effect=ClientError({"Error": {"Code": "403"}}, "GetObject"))
    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=s3)
    client_cm.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.client = MagicMock(return_value=client_cm)

    with patch.object(adapter, "_get_aioboto3_session", return_value=session):
        assert await adapter.sync_flatfile_from_s3("missing", str(tmp_path / "f.csv")) is False


@pytest.mark.asyncio
async def test_massive_list_s3_files(config):
    adapter = MassiveOptionsAdapter(config)
    s3 = AsyncMock()
    s3.list_objects_v2 = AsyncMock(return_value={"Contents": [{"Key": "a"}]})
    client_cm = AsyncMock()
    client_cm.__aenter__ = AsyncMock(return_value=s3)
    client_cm.__aexit__ = AsyncMock(return_value=None)
    session = MagicMock()
    session.client = MagicMock(return_value=client_cm)

    with patch.object(adapter, "_get_aioboto3_session", return_value=session):
        files = await adapter.list_s3_files("prefix/")
        assert files == [{"Key": "a"}]


@pytest.mark.asyncio
async def test_massive_get_options_chain_cache_hit(config):
    adapter = MassiveOptionsAdapter(config)
    cached = OptionChain(symbol="AAPL", expiration_date=date(2026, 1, 1)).model_dump()
    adapter._cache.set("massive_chain_AAPL_2026-01-01", cached, 60)
    chain = await adapter.get_options_chain("AAPL", date(2026, 1, 1))
    assert chain.symbol == "AAPL"


@pytest.mark.asyncio
async def test_massive_format_dataframe_pandas(pandas_config):
    adapter = MassiveOptionsAdapter(pandas_config)
    frame = adapter._format_dataframe(pd.DataFrame({"x": [1]}))
    assert isinstance(frame, pd.DataFrame)


@pytest.mark.asyncio
async def test_massive_format_dataframe_polars(config):
    adapter = MassiveOptionsAdapter(config)
    frame = adapter._format_dataframe(pd.DataFrame({"x": [1]}))
    assert isinstance(frame, pl.DataFrame)


@pytest.mark.asyncio
async def test_massive_describe(config):
    adapter = MassiveOptionsAdapter(config)
    info = await adapter.describe()
    assert info["provider_id"] == "massive"
