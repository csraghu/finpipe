import logging
import os
from datetime import date, datetime
from typing import Any

import aioboto3
import pandas as pd
import polars as pl
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError
from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeDataNotFoundError
from finpipe.core.interfaces import IOptionsProvider
from finpipe.core.models import OptionChain, OptionContract
from finpipe.core.registry import BuildContext, register_provider
from finpipe.network.cache import create_cache_backend
from finpipe.network.resilience import create_resilient_http_client

logger = logging.getLogger(__name__)

API_BASE = "https://api.massive.com"


class MassiveOptionsAdapter(IOptionsProvider):
    def __init__(self, config: FinpipeConfig):
        self._config = config
        self._provider_config = config.providers.massive
        self._provider_config.ensure_configured()
        self._api_key = self._provider_config.api_key
        self._cache = create_cache_backend(config.cache)
        self._client = create_resilient_http_client(
            "massive", self._provider_config.rate_limits, cache_config=config.cache
        )
        self._base_url = "https://api.massive.com/v1"
        self._s3_endpoint = self._provider_config.s3_endpoint or os.environ.get(
            "MASSIVE_S3_ENDPOINT", "https://files.massive.com"
        )
        self._s3_bucket = self._provider_config.s3_bucket or "flatfiles"

    @property
    def api_key(self) -> str | None:
        return self._api_key

    async def close(self) -> None:
        await self._client.close()

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._api_key:
            logger.error("Missing Massive API key — cannot fetch %s", url)
            return {}
        merged = dict(params or {})
        merged["apiKey"] = self._api_key
        response = await self._client.request("GET", url, params=merged)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    async def fetch_options_contracts(self, symbol: str) -> list[dict[str, Any]]:
        ticker = symbol.strip().upper()
        params = {
            "underlying_ticker": ticker,
            "expired": "false",
            "limit": 1000,
        }
        data = await self._get_json(f"{API_BASE}/v3/reference/options/contracts", params)
        return data.get("results", [])

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
        underlying_ticker = symbol.strip().upper()
        params: dict[str, Any] = {"limit": limit}
        if expiration_date:
            params["expiration_date"] = expiration_date
        if contract_type:
            params["contract_type"] = contract_type
        if strike_price_gte is not None:
            params["strike_price.gte"] = strike_price_gte
        if strike_price_lte is not None:
            params["strike_price.lte"] = strike_price_lte
        if sort:
            params["sort"] = sort
        if order:
            params["order"] = order
        data = await self._get_json(
            f"{API_BASE}/v3/snapshot/options/{underlying_ticker}",
            params,
        )
        return data.get("results", [])

    async def fetch_single_option_snapshot(
        self, symbol: str, contract: str
    ) -> dict[str, Any]:
        underlying_ticker = symbol.strip().upper()
        contract_symbol = contract.strip().upper()
        if not contract_symbol.startswith("O:"):
            contract_symbol = f"O:{contract_symbol}"
        data = await self._get_json(
            f"{API_BASE}/v3/snapshot/options/{underlying_ticker}/{contract_symbol}",
        )
        results = data.get("results")
        return results if isinstance(results, dict) else {}

    async def fetch_historical_aggs(
        self, symbol: str, from_date: str, to_date: str
    ) -> list[dict[str, Any]]:
        contract_symbol = symbol.strip().upper()
        if not contract_symbol.startswith("O:"):
            contract_symbol = f"O:{contract_symbol}"
        url = (
            f"{API_BASE}/v2/aggs/ticker/{contract_symbol}/range/"
            f"1/day/{from_date}/{to_date}"
        )
        data = await self._get_json(url, {"adjusted": "true"})
        return data.get("results", [])

    def _get_aioboto3_session(self) -> aioboto3.Session | None:
        access_key = self._provider_config.access_key_id
        secret_key = self._provider_config.secret_access_key
        if not access_key or not secret_key:
            logger.error("Missing S3 credentials — cannot access Massive flatfiles")
            return None
        return aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    async def sync_flatfile_from_s3(
        self, remote_key: str, local_dest_path: str
    ) -> bool:
        session = self._get_aioboto3_session()
        if not session:
            return False
        os.makedirs(os.path.dirname(local_dest_path) or ".", exist_ok=True)
        try:
            async with session.client(
                "s3",
                endpoint_url=self._s3_endpoint,
                config=BotoConfig(signature_version="s3v4"),
            ) as s3:
                response = await s3.get_object(Bucket=self._s3_bucket, Key=remote_key)
                body = await response["Body"].read()
            with open(local_dest_path, "wb") as f:
                f.write(body)
            return True
        except (ClientError, BotoCoreError, OSError, TimeoutError) as exc:
            err_str = str(exc)
            if "403" in err_str or "404" in err_str:
                logger.info(
                    "S3 flatfile not found (or access denied)",
                    extra={"remote_key": remote_key},
                )
            else:
                logger.error(
                    "S3 flatfile download failed",
                    extra={"remote_key": remote_key, "error": err_str},
                )
            return False

    async def list_s3_files(self, prefix: str) -> list[dict[str, Any]]:
        session = self._get_aioboto3_session()
        if not session:
            return []
        try:
            async with session.client(
                "s3",
                endpoint_url=self._s3_endpoint,
                config=BotoConfig(signature_version="s3v4"),
            ) as s3:
                response = await s3.list_objects_v2(
                    Bucket=self._s3_bucket, Prefix=prefix
                )
                return response.get("Contents", [])
        except (ClientError, BotoCoreError, OSError, TimeoutError) as exc:
            logger.exception("Failed to list S3 files", extra={"error": str(exc)})
            return []

    def _format_dataframe(self, df: pd.DataFrame) -> pl.DataFrame | pd.DataFrame:
        if self._config.dataframe_format == "pandas":
            return df
        return pl.from_pandas(df)

    async def get_options_chain(
        self, symbol: str, expiration_date: date | None = None
    ) -> OptionChain:
        cache_key = f"massive_chain_{symbol}_{expiration_date}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return OptionChain(**cached)

        params: dict[str, str] = {"symbol": symbol}
        if expiration_date:
            params["expiration"] = expiration_date.strftime("%Y-%m-%d")
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = await self._client.request(
                "GET", f"{self._base_url}/options/chain", params=params, headers=headers
            )
            data = response.json()
        except Exception as exc:
            logger.warning("Massive options chain request failed: %s", exc)
            raise FinpipeDataNotFoundError(
                f"Failed to fetch options chain from Massive for {symbol}"
            ) from exc

        if not data or "data" not in data:
            raise FinpipeDataNotFoundError(f"No option chain found for {symbol}")

        chain_data = data["data"]

        def _parse_contracts(contracts: list[dict], in_the_money: bool) -> list[OptionContract]:
            return [
                OptionContract(
                    contract_symbol=c.get("contract_symbol", ""),
                    strike=float(c.get("strike", 0.0)),
                    last_price=c.get("last_price"),
                    bid=c.get("bid"),
                    ask=c.get("ask"),
                    volume=c.get("volume"),
                    open_interest=c.get("open_interest"),
                    implied_volatility=c.get("implied_volatility"),
                    in_the_money=c.get("in_the_money", in_the_money),
                )
                for c in contracts
            ]

        exp_dt = datetime.strptime(
            chain_data.get("expiration_date", str(date.today())), "%Y-%m-%d"
        ).date()
        chain = OptionChain(
            symbol=symbol,
            expiration_date=exp_dt,
            calls=_parse_contracts(chain_data.get("calls", []), True),
            puts=_parse_contracts(chain_data.get("puts", []), False),
        )
        self._cache.set(cache_key, chain.model_dump(), self._provider_config.ttls.options_chain_sec)
        return chain

    async def get_options_snapshot(self, symbol: str, **filters) -> pl.DataFrame | pd.DataFrame:
        cache_key = f"massive_snap_{symbol}_{hash(frozenset(filters.items()))}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return self._format_dataframe(pd.DataFrame.from_dict(cached))

        params = {"symbol": symbol, **filters}
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = await self._client.request(
                "GET", f"{self._base_url}/options/snapshot", params=params, headers=headers
            )
            data = response.json()
        except Exception as exc:
            logger.warning("Massive options snapshot request failed: %s", exc)
            raise FinpipeDataNotFoundError(
                f"Failed to fetch options snapshot from Massive for {symbol}"
            ) from exc

        df = pd.DataFrame(data.get("data", []))
        self._cache.set(
            cache_key, df.to_dict(orient="list"), self._provider_config.ttls.options_snapshot_sec
        )
        return self._format_dataframe(df)


@register_provider("massive", category="options")
def build_massive(ctx: BuildContext) -> MassiveOptionsAdapter:
    return MassiveOptionsAdapter(ctx.config)
