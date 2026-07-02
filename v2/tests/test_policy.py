"""Fallback policy contract tests (the rules v1 documented but never enforced)."""

from __future__ import annotations

import polars as pl
import pytest

from finpipe.capabilities.policy import call_with_fallback, is_empty
from finpipe.core.errors import (
    FinpipeConfigError,
    FinpipeDataNotFoundError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)
from finpipe.core.models import OptionChain


def test_is_empty_across_types():
    assert is_empty(None)
    assert is_empty([])
    assert is_empty({})
    assert is_empty(pl.DataFrame())
    assert not is_empty(pl.DataFrame({"a": [1]}))
    assert is_empty(OptionChain(symbol="A", expiration_date="2026-01-16"))
    assert not is_empty([1])


async def test_falls_back_on_data_not_found():
    async def primary():
        raise FinpipeDataNotFoundError("miss")

    async def fallback():
        return ["ok"]

    result = await call_with_fallback("t", [("p", primary), ("f", fallback)])
    assert result == ["ok"]


async def test_falls_back_on_empty_result():
    async def primary():
        return []

    async def fallback():
        return ["ok"]

    assert await call_with_fallback("t", [("p", primary), ("f", fallback)]) == ["ok"]


async def test_never_falls_back_on_rate_limit():
    calls = {"fallback": 0}

    async def primary():
        raise FinpipeRateLimitExceededError("throttled")

    async def fallback():
        calls["fallback"] += 1
        return ["ok"]

    with pytest.raises(FinpipeRateLimitExceededError):
        await call_with_fallback("t", [("p", primary), ("f", fallback)])
    assert calls["fallback"] == 0  # v1 hammered the fallback here


async def test_never_falls_back_on_config_error():
    async def primary():
        raise FinpipeConfigError("missing key")

    async def fallback():
        return ["ok"]

    with pytest.raises(FinpipeConfigError):
        await call_with_fallback("t", [("p", primary), ("f", fallback)])


async def test_all_empty_returns_empty_not_error():
    async def primary():
        return []

    async def fallback():
        return []

    assert await call_with_fallback("t", [("p", primary), ("f", fallback)]) == []


async def test_all_failed_raises_last_error():
    async def primary():
        raise FinpipeProviderDownError("p down")

    async def fallback():
        raise FinpipeDataNotFoundError("f miss")

    with pytest.raises(FinpipeDataNotFoundError):
        await call_with_fallback("t", [("p", primary), ("f", fallback)])
