from datetime import date

import httpx
import pytest
import respx

from finpipe.providers.fred import FredAdapter


@pytest.mark.asyncio
async def test_fred_macro_series(config):
    adapter = FredAdapter(config)

    json_mock = {
        "observations": [
            {"date": "2023-01-01", "value": "4.5"},
            {"date": "2023-01-02", "value": "."},  # Test missing value
        ]
    }

    with respx.mock:
        respx.get(url__startswith="https://api.stlouisfed.org").mock(
            return_value=httpx.Response(200, json=json_mock)
        )
        df = await adapter.get_macro_series("DGS10", date(2023, 1, 1), date(2023, 1, 2))
        assert df.height == 1
        assert df.select("value").item() == 4.5
