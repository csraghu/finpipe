"""Contract tests: every cacheable type round-trips exactly (review §2.2/§2.3 guard)."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import polars as pl
import pytest

from finpipe.runtime import codec


def test_plain_json_round_trip():
    value = {"a": 1, "b": [1.5, "x", None, True]}
    assert codec.loads(codec.dumps(value)) == value


def test_datetime_and_date_round_trip():
    value = {"ts": datetime(2026, 1, 2, 3, 4, 5), "d": date(2026, 1, 2), "nested": [datetime(2020, 1, 1)]}
    restored = codec.loads(codec.dumps(value))
    assert restored["ts"] == datetime(2026, 1, 2, 3, 4, 5)
    assert restored["d"] == date(2026, 1, 2)
    assert restored["nested"][0] == datetime(2020, 1, 1)


def test_pandas_timestamp_records_round_trip():
    """The exact payload shape that silently broke v1's SQLite cache."""
    records = [
        {"timestamp": pd.Timestamp("2026-01-02 00:00:00"), "close": 101.5, "volume": 1000},
        {"timestamp": pd.Timestamp("2026-01-03 00:00:00"), "close": 102.0, "volume": 900},
    ]
    restored = codec.loads(codec.dumps(records))
    assert restored[0]["timestamp"] == datetime(2026, 1, 2)
    assert restored[1]["close"] == 102.0


def test_nan_becomes_none():
    restored = codec.loads(codec.dumps({"v": float("nan"), "w": float("inf")}))
    assert restored == {"v": None, "w": None}


def test_polars_dataframe_round_trip():
    df = pl.DataFrame({"timestamp": [datetime(2026, 1, 2)], "close": [1.5], "volume": [10]})
    restored = codec.loads(codec.dumps(df))
    assert isinstance(restored, pl.DataFrame)
    assert restored.columns == df.columns
    assert restored.rows() == df.rows()


def test_pandas_dataframe_round_trip():
    df = pd.DataFrame({"timestamp": pd.to_datetime(["2026-01-02"]), "close": [1.5]})
    restored = codec.loads(codec.dumps(df))
    assert isinstance(restored, pd.DataFrame)
    assert list(restored.columns) == list(df.columns)
    assert restored["close"].tolist() == [1.5]


def test_unsupported_type_raises_loudly():
    class Opaque: ...

    with pytest.raises(codec.CodecError):
        codec.dumps({"bad": Opaque()})


def test_digest_key_is_stable_and_distinct():
    a1 = codec.digest_key("AAPL", "2026-01-01", "1d")
    a2 = codec.digest_key("AAPL", "2026-01-01", "1d")
    b = codec.digest_key("AAPL", "2026-01-02", "1d")
    assert a1 == a2
    assert a1 != b
    assert len(a1) == 64  # sha256 hex — never the salted builtin hash()
