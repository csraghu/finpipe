"""Shared option-chain → snapshot conversion (v1 duplicated this across adapters)."""

from __future__ import annotations

import pandas as pd
import polars as pl

from ..core.models import OptionChain

_SNAPSHOT_COLUMNS = (
    "contract_symbol", "contract_type", "strike", "expiration_date",
    "last_price", "bid", "ask", "volume", "open_interest", "implied_volatility",
)


def chain_to_snapshot_frame(chain: OptionChain, dataframe_format: str) -> pl.DataFrame | pd.DataFrame:
    rows = []
    for contract_type, contracts in (("call", chain.calls), ("put", chain.puts)):
        for c in contracts:
            rows.append(
                {
                    "contract_symbol": c.contract_symbol,
                    "contract_type": contract_type,
                    "strike": c.strike,
                    "expiration_date": chain.expiration_date.isoformat(),
                    "last_price": c.last_price,
                    "bid": c.bid,
                    "ask": c.ask,
                    "volume": c.volume,
                    "open_interest": c.open_interest,
                    "implied_volatility": c.implied_volatility,
                }
            )
    df = pd.DataFrame(rows, columns=list(_SNAPSHOT_COLUMNS))
    if dataframe_format == "pandas":
        return df
    return pl.from_pandas(df)
