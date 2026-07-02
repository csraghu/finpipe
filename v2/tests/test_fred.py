"""Reference-adapter tests: FRED end-to-end through the v2 pattern."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from finpipe.core.config import FredConfig
from finpipe.core.errors import FinpipeConfigError, FinpipeParseError
from finpipe.providers.fred import FredAdapter
from pydantic import SecretStr

from conftest import FakeExecutor, FakeResponse, make_runtime

_PAYLOAD = {
    "observations": [
        {"date": "2026-01-02", "value": "4.25"},
        {"date": "2026-01-03", "value": "."},  # FRED missing-data marker
        {"date": "2026-01-04", "value": "4.30"},
    ]
}


def _adapter(executor: FakeExecutor, *, api_key: str | None = "test-key") -> FredAdapter:
    config = FredConfig(api_key=SecretStr(api_key) if api_key else None)
    return FredAdapter(make_runtime(config, executor, provider_key="fred"))


async def test_missing_key_raises_config_error_on_first_use_not_construction():
    adapter = _adapter(FakeExecutor(), api_key=None)  # construction is fine
    with pytest.raises(FinpipeConfigError, match="FRED_API_KEY"):
        await adapter.get_macro_series("DGS10", date(2026, 1, 1), date(2026, 1, 31))


async def test_series_normalized_schema_and_missing_marker_filtered():
    adapter = _adapter(FakeExecutor([FakeResponse(200, json_data=_PAYLOAD)]))
    frame = await adapter.get_macro_series("DGS10", date(2026, 1, 1), date(2026, 1, 31))
    assert isinstance(frame, pl.DataFrame)
    assert frame.columns == ["timestamp", "value"]
    assert frame.height == 2  # "." row dropped
    assert frame["value"].to_list() == [4.25, 4.30]


async def test_cache_hit_equals_fresh_fetch():
    """The v1 killer: cached result must be identical to the fresh one."""
    executor = FakeExecutor([FakeResponse(200, json_data=_PAYLOAD)])
    adapter = _adapter(executor)
    fresh = await adapter.get_macro_series("DGS10", date(2026, 1, 1), date(2026, 1, 31))
    cached = await adapter.get_macro_series("DGS10", date(2026, 1, 1), date(2026, 1, 31))
    assert len(executor.calls) == 1  # second call served from cache
    assert cached.columns == fresh.columns
    assert cached.rows() == fresh.rows()


async def test_malformed_payload_raises_parse_error():
    adapter = _adapter(FakeExecutor([FakeResponse(200, json_data={"nope": []})]))
    with pytest.raises(FinpipeParseError):
        await adapter.get_macro_series("DGS10", date(2026, 1, 1), date(2026, 1, 31))


async def test_api_key_sent_as_param_never_in_path():
    executor = FakeExecutor([FakeResponse(200, json_data=_PAYLOAD)])
    adapter = _adapter(executor)
    await adapter.get_macro_series("DGS10", date(2026, 1, 1), date(2026, 1, 31))
    method, url, kwargs = executor.calls[0]
    assert "test-key" not in url
    assert kwargs["params"]["api_key"] == "test-key"
