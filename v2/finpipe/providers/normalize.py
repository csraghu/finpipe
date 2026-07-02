"""Canonical schema builders — ONE implementation (v1 had five `_format_dataframe`s).

Both the fresh-fetch path and the cache-hit path go through these builders,
which is what guarantees fresh == cached output (review §2.3).
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import polars as pl

OHLCV_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
MACRO_COLUMNS = ("timestamp", "value")


def macro_frame(records: list[dict[str, Any]], dataframe_format: str) -> pl.DataFrame | pd.DataFrame:
    """records: [{"timestamp": datetime, "value": float}, ...] → canonical frame."""
    df = pd.DataFrame(records, columns=list(MACRO_COLUMNS))
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["value"] = df["value"].astype(float)
    if dataframe_format == "pandas":
        return df
    return pl.from_pandas(df)


def ohlcv_frame(records: list[dict[str, Any]], dataframe_format: str) -> pl.DataFrame | pd.DataFrame:
    """records with keys OHLCV_COLUMNS → canonical frame (schema always present)."""
    df = pd.DataFrame(records, columns=list(OHLCV_COLUMNS))
    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        for col in ("open", "high", "low", "close"):
            df[col] = df[col].astype(float)
    if dataframe_format == "pandas":
        return df
    return pl.from_pandas(df)
