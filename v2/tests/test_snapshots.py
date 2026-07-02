"""Snapshots converter tests: option chain → DataFrame normalization."""

from __future__ import annotations

from datetime import date

import pandas as pd
import polars as pl
from finpipe.core.models import OptionChain, OptionContract
from finpipe.providers.snapshots import chain_to_snapshot_frame


def test_chain_to_snapshot_frame_pandas():
    """Convert option chain to pandas snapshot frame."""
    chain = OptionChain(
        symbol="AAPL",
        expiration_date=date(2026, 1, 17),
        calls=[
            OptionContract(
                contract_symbol="AAPL260117C00150000",
                strike=150.0,
                last_price=10.5,
                bid=10.4,
                ask=10.6,
                volume=100,
                open_interest=500,
                implied_volatility=0.2,
            )
        ],
        puts=[
            OptionContract(
                contract_symbol="AAPL260117P00150000",
                strike=150.0,
                last_price=9.5,
                bid=9.4,
                ask=9.6,
                volume=80,
                open_interest=400,
                implied_volatility=0.19,
            )
        ],
    )

    frame = chain_to_snapshot_frame(chain, "pandas")

    assert isinstance(frame, pd.DataFrame)
    assert frame.shape == (2, 10)
    assert list(frame.columns) == [
        "contract_symbol", "contract_type", "strike", "expiration_date",
        "last_price", "bid", "ask", "volume", "open_interest", "implied_volatility",
    ]
    assert frame.iloc[0]["contract_type"] == "call"
    assert frame.iloc[1]["contract_type"] == "put"


def test_chain_to_snapshot_frame_polars():
    """Convert option chain to polars snapshot frame."""
    chain = OptionChain(
        symbol="AAPL",
        expiration_date=date(2026, 1, 17),
        calls=[
            OptionContract(
                contract_symbol="AAPL260117C00150000",
                strike=150.0,
                last_price=10.5,
                bid=10.4,
                ask=10.6,
                volume=100,
                open_interest=500,
                implied_volatility=0.2,
            )
        ],
        puts=[],
    )

    frame = chain_to_snapshot_frame(chain, "polars")

    assert isinstance(frame, pl.DataFrame)
    assert frame.shape == (1, 10)
    assert frame["contract_type"][0] == "call"
    assert frame["strike"][0] == 150.0


def test_empty_chain_empty_frame():
    """Empty chain returns empty frame."""
    chain = OptionChain(
        symbol="AAPL",
        expiration_date=date(2026, 1, 17),
        calls=[],
        puts=[],
    )

    frame = chain_to_snapshot_frame(chain, "pandas")

    assert isinstance(frame, pd.DataFrame)
    assert frame.shape[0] == 0
    assert len(frame.columns) == 10


def test_chain_expiration_date_isoformat():
    """Expiration date is stored as ISO format string."""
    chain = OptionChain(
        symbol="AAPL",
        expiration_date=date(2026, 3, 20),
        calls=[
            OptionContract(
                contract_symbol="TEST",
                strike=100.0,
                last_price=5.0,
                bid=4.9,
                ask=5.1,
            )
        ],
        puts=[],
    )

    frame = chain_to_snapshot_frame(chain, "pandas")

    assert frame.iloc[0]["expiration_date"] == "2026-03-20"


def test_all_contracts_in_snapshot():
    """All call and put contracts are included in snapshot."""
    chain = OptionChain(
        symbol="AAPL",
        expiration_date=date(2026, 1, 17),
        calls=[
            OptionContract(contract_symbol="C1", strike=150.0, last_price=10.0, bid=9.9, ask=10.1),
            OptionContract(contract_symbol="C2", strike=155.0, last_price=8.0, bid=7.9, ask=8.1),
        ],
        puts=[
            OptionContract(contract_symbol="P1", strike=150.0, last_price=9.0, bid=8.9, ask=9.1),
        ],
    )

    frame = chain_to_snapshot_frame(chain, "pandas")

    assert frame.shape[0] == 3
    call_rows = frame[frame["contract_type"] == "call"]
    put_rows = frame[frame["contract_type"] == "put"]
    assert len(call_rows) == 2
    assert len(put_rows) == 1
