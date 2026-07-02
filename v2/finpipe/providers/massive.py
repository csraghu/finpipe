"""Massive options adapter (REST + S3 flatfiles).

v2 fixes vs v1:
- honest taxonomy: classified errors from the executor pass through untouched
  (v1 wrapped rate-limit errors as ``DataNotFound``, breaking fallback rules)
- missing API key raises ``FinpipeConfigError`` on first use (v1 logged and
  returned ``{}`` silently)
- digest-based cache keys (v1 used salted ``hash(frozenset(...))``)
- an empty chain is returned as an empty ``OptionChain`` — the capability layer's
  fallback policy treats empty results, adapters don't invent errors for them
- ``aioboto3`` imported lazily (optional extra)
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any

from ..core.config import MassiveConfig
from ..core.errors import FinpipeConfigError
from ..core.models import OptionChain, OptionContract
from ..core.protocols import DataFrameLike
from .base import ProviderAdapter, ProviderRuntime
from .manifest import provider

logger = logging.getLogger(__name__)

_API_BASE = "https://api.massive.com"
_DEFAULT_S3_ENDPOINT = "https://files.massive.com"
_DEFAULT_S3_BUCKET = "flatfiles"


class MassiveOptionsAdapter(ProviderAdapter):
    def __init__(self, runtime: ProviderRuntime) -> None:
        super().__init__(runtime)
        self._config: MassiveConfig = runtime.config

    def _ensure_configured(self) -> None:
        if self._config.api_key is None:
            raise FinpipeConfigError(
                "Massive requires MASSIVE_API_KEY; set it or disable providers.massive"
            )
        super()._ensure_configured()

    def _key(self) -> str:
        assert self._config.api_key is not None
        return self._config.api_key.get_secret_value()

    async def describe(self) -> dict[str, Any]:
        from ..observe.describe import provider_descriptor

        return provider_descriptor(
            "massive", "options", self._config,
            configured=self._config.api_key is not None,
            details={
                "api_base_url": f"{_API_BASE}/v3",
                "s3_endpoint": self._config.s3_endpoint or _DEFAULT_S3_ENDPOINT,
                "s3_bucket": self._config.s3_bucket or _DEFAULT_S3_BUCKET,
                "s3_configured": bool(self._config.access_key_id and self._config.secret_access_key),
            },
        )

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._validated:
            self._ensure_configured()
        merged = dict(params or {})
        merged["apiKey"] = self._key()
        response = await self._rt.executor.request("GET", url, params=merged)
        data = response.json()
        return data if isinstance(data, dict) else {}

    # -- raw REST surface (provider-specific; reachable via catalog provider ref) ----
    async def fetch_options_contracts(self, symbol: str) -> list[dict[str, Any]]:
        data = await self._get_json(
            f"{_API_BASE}/v3/reference/options/contracts",
            {"underlying_ticker": symbol.strip().upper(), "expired": "false", "limit": 1000},
        )
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
            f"{_API_BASE}/v3/snapshot/options/{symbol.strip().upper()}", params
        )
        return data.get("results", [])

    async def fetch_single_option_snapshot(self, symbol: str, contract: str) -> dict[str, Any]:
        contract_symbol = contract.strip().upper()
        if not contract_symbol.startswith("O:"):
            contract_symbol = f"O:{contract_symbol}"
        data = await self._get_json(
            f"{_API_BASE}/v3/snapshot/options/{symbol.strip().upper()}/{contract_symbol}"
        )
        results = data.get("results")
        return results if isinstance(results, dict) else {}

    async def fetch_historical_aggs(
        self, symbol: str, from_date: str, to_date: str
    ) -> list[dict[str, Any]]:
        contract_symbol = symbol.strip().upper()
        if not contract_symbol.startswith("O:"):
            contract_symbol = f"O:{contract_symbol}"
        data = await self._get_json(
            f"{_API_BASE}/v2/aggs/ticker/{contract_symbol}/range/1/day/{from_date}/{to_date}",
            {"adjusted": "true"},
        )
        return data.get("results", [])

    # -- IOptionsProvider -----------------------------------------------------------
    async def get_options_chain(self, symbol: str, expiration_date: date | None = None) -> OptionChain:
        async def fetch() -> dict[str, Any]:
            exp = expiration_date.isoformat() if expiration_date else None
            results = await self.fetch_options_snapshot(symbol, expiration_date=exp)
            return _chain_from_snapshot(symbol, results).model_dump()

        cached = await self.cached_fetch(
            "options_chain",
            (symbol, expiration_date.isoformat() if expiration_date else "front"),
            self._config.ttls.options_chain_sec,
            fetch,
        )
        return OptionChain.model_validate(cached)

    async def get_options_snapshot(self, symbol: str, **filters: Any) -> DataFrameLike:
        async def fetch() -> list[dict[str, Any]]:
            results = await self.fetch_options_snapshot(symbol, **filters)
            return [_flatten_snapshot_row(r) for r in results]

        canonical_filters = tuple(sorted((k, str(v)) for k, v in filters.items()))
        rows = await self.cached_fetch(
            "options_snapshot", (symbol, *["=".join(p) for p in canonical_filters]),
            self._config.ttls.options_snapshot_sec, fetch,
        )
        import pandas as pd
        import polars as pl

        df = pd.DataFrame(rows)
        return df if self._rt.dataframe_format == "pandas" else pl.from_pandas(df)

    # -- S3 flatfiles ------------------------------------------------------------------
    def _s3_session(self) -> Any:
        if not (self._config.access_key_id and self._config.secret_access_key):
            raise FinpipeConfigError(
                "Massive S3 requires MASSIVE_ACCESS_KEY_ID and MASSIVE_SECRET_ACCESS_KEY"
            )
        import aioboto3  # lazy: optional extra

        return aioboto3.Session(
            aws_access_key_id=self._config.access_key_id.get_secret_value(),
            aws_secret_access_key=self._config.secret_access_key.get_secret_value(),
        )

    async def sync_flatfile_from_s3(self, remote_key: str, local_dest_path: str) -> bool:
        from botocore.config import Config as BotoConfig
        from botocore.exceptions import BotoCoreError, ClientError

        session = self._s3_session()
        os.makedirs(os.path.dirname(local_dest_path) or ".", exist_ok=True)
        try:
            async with session.client(
                "s3",
                endpoint_url=self._config.s3_endpoint or _DEFAULT_S3_ENDPOINT,
                config=BotoConfig(signature_version="s3v4"),
            ) as s3:
                response = await s3.get_object(
                    Bucket=self._config.s3_bucket or _DEFAULT_S3_BUCKET, Key=remote_key
                )
                body = await response["Body"].read()
            with open(local_dest_path, "wb") as fh:
                fh.write(body)
            return True
        except (ClientError, BotoCoreError, OSError, TimeoutError) as exc:
            level = logging.INFO if any(code in str(exc) for code in ("403", "404")) else logging.ERROR
            logger.log(level, "S3 flatfile download failed for %s: %s", remote_key, exc)
            return False

    async def list_s3_files(self, prefix: str) -> list[dict[str, Any]]:
        from botocore.config import Config as BotoConfig
        from botocore.exceptions import BotoCoreError, ClientError

        session = self._s3_session()
        try:
            async with session.client(
                "s3",
                endpoint_url=self._config.s3_endpoint or _DEFAULT_S3_ENDPOINT,
                config=BotoConfig(signature_version="s3v4"),
            ) as s3:
                response = await s3.list_objects_v2(
                    Bucket=self._config.s3_bucket or _DEFAULT_S3_BUCKET, Prefix=prefix
                )
                return response.get("Contents", [])
        except (ClientError, BotoCoreError, OSError, TimeoutError) as exc:
            logger.error("Failed to list S3 files under %s: %s", prefix, exc)
            return []


def _chain_from_snapshot(symbol: str, results: list[dict[str, Any]]) -> OptionChain:
    calls: list[OptionContract] = []
    puts: list[OptionContract] = []
    target_exp: str | None = None
    for row in results:
        details = row.get("details", {})
        exp = details.get("expiration_date")
        if target_exp is None and exp:
            target_exp = exp
        if target_exp and exp != target_exp:
            continue
        day = row.get("day", {})
        quote = row.get("last_quote", {})
        contract = OptionContract(
            contract_symbol=details.get("ticker", ""),
            strike=float(details.get("strike_price", 0.0)),
            last_price=day.get("close"),
            bid=quote.get("bid"),
            ask=quote.get("ask"),
            volume=day.get("volume"),
            open_interest=row.get("open_interest"),
            implied_volatility=row.get("implied_volatility"),
        )
        kind = str(details.get("contract_type", "")).lower()
        if kind == "call":
            calls.append(contract)
        elif kind == "put":
            puts.append(contract)
    exp_date = (
        datetime.strptime(target_exp, "%Y-%m-%d").date() if target_exp else date.today()
    )
    return OptionChain(symbol=symbol, expiration_date=exp_date, calls=calls, puts=puts)


def _flatten_snapshot_row(row: dict[str, Any]) -> dict[str, Any]:
    details = row.get("details", {})
    day = row.get("day", {})
    quote = row.get("last_quote", {})
    return {
        "contract_symbol": details.get("ticker", ""),
        "contract_type": details.get("contract_type", ""),
        "strike": float(details.get("strike_price", 0.0)),
        "expiration_date": details.get("expiration_date", ""),
        "last_price": day.get("close"),
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "volume": day.get("volume"),
        "open_interest": row.get("open_interest"),
        "implied_volatility": row.get("implied_volatility"),
    }


@provider(
    "massive",
    capability="options",
    config_attr="massive",
    label="Massive",
    description="High-fidelity options snapshots, contracts, aggregates, and S3 flatfiles",
    secrets=("MASSIVE_API_KEY",),
    extra="massive",
    probe="options.massive",
)
def build_massive(runtime: ProviderRuntime) -> MassiveOptionsAdapter:
    return MassiveOptionsAdapter(runtime)
