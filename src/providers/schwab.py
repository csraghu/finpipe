import base64
import logging
import time
from datetime import date
from typing import Any

import pandas as pd
import polars as pl
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeDataNotFoundError, FinpipeProviderDownError
from finpipe.core.interfaces import (
    IHistoricalPriceProvider,
    IMetadataProvider,
    IOptionsProvider,
    IProviderDescribe,
)
from finpipe.core.models import OptionChain, OptionContract, TickerMetadata
from finpipe.core.registry import register_provider
from finpipe.network.cache_manager import resolve_cache_backend
from finpipe.network.resilience import create_resilient_http_client
from finpipe.providers.descriptor import provider_descriptor

logger = logging.getLogger(__name__)


@register_provider("schwab", category="equity")
class SchwabAdapter(
    IHistoricalPriceProvider, IMetadataProvider, IOptionsProvider, IProviderDescribe
):
    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._provider_config = config.providers.schwab
        self._provider_config.ensure_configured()

        self._app_key = self._provider_config.app_key
        self._app_secret = self._provider_config.app_secret
        self._refresh_token = self._provider_config.refresh_token

        self._cache = resolve_cache_backend(config.cache)
        self._client = create_resilient_http_client(
            "schwab", self._provider_config.rate_limits, cache_config=config.cache
        )
        self._base_url = "https://api.schwabapi.com/marketdata/v1"
        self._auth_url = "https://api.schwabapi.com/v1/oauth/token"

    async def describe(self) -> dict[str, Any]:
        cfg = self._provider_config
        return provider_descriptor(
            provider_id="schwab",
            capability=["equity", "options"],
            provider_config=cfg,
            configured=bool(self._app_key and self._refresh_token),
            details={"api_base_url": self._base_url},
        )

    async def _get_access_token(self) -> str:
        cache_key = "schwab_access_token"
        cached = self._cache.get(cache_key)
        if cached:
            return str(cached)

        auth_str = f"{self._app_key}:{self._app_secret}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()

        headers = {
            "Authorization": f"Basic {b64_auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token
        }

        try:
            resp = await self._client.post(self._auth_url, headers=headers, data=data)
            resp.raise_for_status()
            token_data = resp.json()
            access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 1800)
            # Buffer of 60 seconds
            self._cache.set(cache_key, access_token, max(1, expires_in - 60))
            return access_token
        except Exception as exc:
            logger.error("Failed to fetch Schwab access token: %s", exc)
            raise FinpipeProviderDownError("Schwab OAuth failed") from exc

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = await self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{self._base_url}/{endpoint}"
        resp = await self._client.get(url, headers=headers, params=params)
        if resp.status_code == 404:
            raise FinpipeDataNotFoundError(f"Schwab data not found at {endpoint}")
        resp.raise_for_status()
        return resp.json()

    def _format_dataframe(self, df: pd.DataFrame) -> pl.DataFrame | pd.DataFrame:
        if self._config.dataframe_format == "pandas":
            return df
        return pl.from_pandas(df)

    async def get_historical_prices(
        self, symbol: str, start_date: date, end_date: date, interval: str = "1d"
    ) -> pl.DataFrame | pd.DataFrame:
        cache_key = f"schwab_hist_{symbol}_{start_date}_{end_date}_{interval}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return self._format_dataframe(pd.DataFrame.from_dict(cached))

        period_type = "month" if interval == "1mo" else "day"
        frequency_type = "monthly" if interval == "1mo" else "daily"

        params = {
            "symbol": symbol,
            "periodType": period_type,
            "frequencyType": frequency_type,
            "frequency": 1,
            "startDate": int(time.mktime(start_date.timetuple()) * 1000),
            "endDate": int(time.mktime(end_date.timetuple()) * 1000),
        }

        data = await self._get("pricehistory", params=params)
        candles = data.get("candles", [])
        if not candles:
            raise FinpipeDataNotFoundError(f"No historical prices found for {symbol}")

        df = pd.DataFrame(candles)
        df.rename(columns={"datetime": "timestamp"}, inplace=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")

        self._cache.set(
            cache_key,
            df.to_dict(orient="list"),
            self._provider_config.ttls.historical_prices_sec,
        )
        return self._format_dataframe(df)

    async def get_live_spot_price(self, symbol: str) -> float | None:
        cache_key = f"schwab_spot_{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return float(cached)

        params = {"symbols": symbol}
        data = await self._get("quotes", params=params)

        quote = data.get(symbol, {}).get("quote", {})
        price = quote.get("lastPrice")
        if price is not None:
            self._cache.set(cache_key, price, self._provider_config.ttls.live_spot_price_sec)
        return price

    async def get_metadata(self, symbol: str) -> TickerMetadata:
        cache_key = f"schwab_meta_{symbol}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return TickerMetadata(**cached)

        params = {"symbols": symbol}
        data = await self._get("quotes", params=params)

        asset = data.get(symbol, {})
        if not asset:
            raise FinpipeDataNotFoundError(f"Schwab metadata not found for {symbol}")

        ref = asset.get("reference", {})
        metadata = TickerMetadata(
            symbol=symbol,
            short_name=ref.get("description"),
            long_name=ref.get("description"),
            exchange=ref.get("exchangeName"),
        )
        self._cache.set(
            cache_key, metadata.model_dump(), self._provider_config.ttls.metadata_sec
        )
        return metadata

    async def get_options_chain(
        self, symbol: str, expiration_date: date | None = None
    ) -> OptionChain:
        cache_key = f"schwab_chain_{symbol}_{expiration_date}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return OptionChain(**cached)

        params = {"symbol": symbol}
        if expiration_date:
            params["fromDate"] = expiration_date.isoformat()
            params["toDate"] = expiration_date.isoformat()

        data = await self._get("chains", params=params)
        if data.get("status") != "SUCCESS":
            raise FinpipeDataNotFoundError(f"No option chain found for {symbol}")

        def _parse_map(map_data: dict, in_the_money: bool) -> list[OptionContract]:
            contracts = []
            for exp_date_key, strike_map in map_data.items():
                for strike, contract_list in strike_map.items():
                    for contract in contract_list:
                        contracts.append(
                            OptionContract(
                                contract_symbol=contract.get("symbol", ""),
                                strike=float(contract.get("strikePrice", 0.0)),
                                last_price=float(contract.get("last", 0.0)),
                                bid=float(contract.get("bid", 0.0)),
                                ask=float(contract.get("ask", 0.0)),
                                volume=int(contract.get("totalVolume", 0)),
                                open_interest=int(contract.get("openInterest", 0)),
                                implied_volatility=float(contract.get("volatility", 0.0)),
                                in_the_money=bool(contract.get("inTheMoney", in_the_money)),
                            )
                        )
            return contracts

        calls = _parse_map(data.get("callExpDateMap", {}), True)
        puts = _parse_map(data.get("putExpDateMap", {}), False)

        target_exp = expiration_date or date.today()
        if calls and not expiration_date:
            pass # Simplified

        chain = OptionChain(
            symbol=symbol,
            expiration_date=target_exp,
            calls=calls,
            puts=puts,
        )
        self._cache.set(
            cache_key, chain.model_dump(), self._provider_config.ttls.options_chain_sec
        )
        return chain

    async def get_options_snapshot(self, symbol: str, **filters) -> pl.DataFrame | pd.DataFrame:
        chain = await self.get_options_chain(symbol)
        data = []
        for call in chain.calls:
            row = call.model_dump()
            row["type"] = "CALL"
            data.append(row)
        for put in chain.puts:
            row = put.model_dump()
            row["type"] = "PUT"
            data.append(row)
        return self._format_dataframe(pd.DataFrame(data))

    async def fetch_options_contracts(self, symbol: str) -> list[dict[str, Any]]:
        chain = await self.get_options_chain(symbol)
        contracts = []
        for call in chain.calls:
            contracts.append(
                {
                    "contract_type": "call",
                    "strike_price": call.strike,
                    "ticker": call.contract_symbol,
                    "expiration_date": chain.expiration_date.isoformat(),
                }
            )
        for put in chain.puts:
            contracts.append(
                {
                    "contract_type": "put",
                    "strike_price": put.strike,
                    "ticker": put.contract_symbol,
                    "expiration_date": chain.expiration_date.isoformat(),
                }
            )
        return contracts

    async def fetch_options_snapshot(
        self,
        symbol: str,
        expiration_date: str | None = None,
        contract_type: str | None = None,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        limit: int = 250,
        sort: str | None = None,
        order: str | None = None,
    ) -> list[dict[str, Any]]:
        exp = date.fromisoformat(expiration_date) if expiration_date else None
        chain = await self.get_options_chain(symbol, exp)
        expiration = chain.expiration_date.isoformat()
        snapshots = []
        sides = []
        if contract_type in (None, "call"):
            sides.append(("call", list(chain.calls)))
        if contract_type in (None, "put"):
            sides.append(("put", list(chain.puts)))

        for _side, rows in sides:
            filtered = list(rows)
            if strike_price_gte is not None:
                filtered = [c for c in filtered if c.strike >= strike_price_gte]
            if strike_price_lte is not None:
                filtered = [c for c in filtered if c.strike <= strike_price_lte]
            for contract in filtered[:limit]:
                snapshots.append(
                    {
                        "details": {"ticker": contract.contract_symbol},
                        "day": {"close": contract.last_price, "volume": contract.volume},
                        "open_interest": contract.open_interest,
                        "implied_volatility": contract.implied_volatility,
                        "last_quote": {"bid": contract.bid, "ask": contract.ask},
                        "expiration_date": expiration,
                    }
                )
                if len(snapshots) >= limit:
                    return snapshots
        return snapshots

    async def close(self) -> None:
        await self._client.aclose()
