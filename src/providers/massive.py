import logging
from datetime import date, datetime

import pandas as pd
import polars as pl

from finpipe.core.config import FinpipeConfig
from finpipe.core.exceptions import FinpipeDataNotFoundError
from finpipe.core.interfaces import IOptionsProvider
from finpipe.core.models import OptionChain, OptionContract
from finpipe.core.registry import BuildContext, register_provider
from finpipe.network.cache import create_cache_backend
from finpipe.network.resilience import create_resilient_http_client

logger = logging.getLogger(__name__)


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

    async def close(self) -> None:
        await self._client.close()

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
