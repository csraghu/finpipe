import pytest
from finpipe._internal.aimd import (
    AIMD_ADDITIVE_INCREASE_RPS,
    AIMD_BURST_CAPACITY,
    AIMD_MIN_RATE_RPS,
    AIMD_MULTIPLICATIVE_DECREASE,
    AIMD_SUCCESSES_BEFORE_INCREASE,
)
from finpipe.core.config import RateLimitConfig
from finpipe.network.limiter import AdaptiveRateLimiter, build_adaptive_limiter


def test_build_adaptive_limiter_uses_hard_cap_only(tmp_path):
    config = RateLimitConfig(max_requests_per_second=5.0)
    db_path = str(tmp_path / "rates.db")
    limiter = build_adaptive_limiter("test_ns", config, db_path=db_path)
    assert limiter.hard_cap == 5.0
    assert limiter.min_rate == AIMD_MIN_RATE_RPS
    assert limiter.rate == 1.0
    assert limiter.capacity == AIMD_BURST_CAPACITY
    assert limiter.additive_increase == AIMD_ADDITIVE_INCREASE_RPS
    assert limiter.multiplicative_decrease == AIMD_MULTIPLICATIVE_DECREASE
    assert limiter.successes_before_increase == AIMD_SUCCESSES_BEFORE_INCREASE


def test_rate_limit_config_rejects_aimd_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RateLimitConfig.model_validate(
            {
                "max_requests_per_second": 5.0,
                "min_requests_per_second": 0.2,
            }
        )


def test_adaptive_limiter_persists_rate_across_sessions(tmp_path):
    db_path = str(tmp_path / "rates.db")
    limiter = AdaptiveRateLimiter("persist", hard_cap_rps=4.0, db_path=db_path)
    limiter.learned_max = 4.0
    for _ in range(AIMD_SUCCESSES_BEFORE_INCREASE):
        limiter.record_success()
    saved_rate = limiter.rate

    reloaded = AdaptiveRateLimiter("persist", hard_cap_rps=4.0, db_path=db_path)
    assert reloaded.rate == saved_rate


def test_adaptive_limiter_record_429_decreases_rate(tmp_path):
    db_path = str(tmp_path / "rates.db")
    limiter = AdaptiveRateLimiter(namespace="test", hard_cap_rps=4.0, db_path=db_path)
    limiter.rate = 4.0
    limiter.record_429()
    assert limiter.rate == 4.0 * AIMD_MULTIPLICATIVE_DECREASE


def test_adaptive_limiter_record_success_increases_rate(tmp_path):
    db_path = str(tmp_path / "rates.db")
    limiter = AdaptiveRateLimiter(namespace="test", hard_cap_rps=4.0, db_path=db_path)
    limiter.rate = 1.0
    limiter.learned_max = 4.0
    limiter.successes_before_increase = 1
    limiter.record_success()
    assert limiter.rate == 1.0 + AIMD_ADDITIVE_INCREASE_RPS


@pytest.mark.asyncio
async def test_adaptive_limiter_acquire(tmp_path):
    db_path = str(tmp_path / "rates.db")
    limiter = AdaptiveRateLimiter(namespace="test_acquire", hard_cap_rps=100.0, db_path=db_path)
    limiter.rate = 100.0
    await limiter.acquire()
    assert limiter.tokens < float(AIMD_BURST_CAPACITY)


def test_adaptive_limiter_closes_sqlite_connections(monkeypatch, tmp_path):
    import sqlite3
    from contextlib import closing

    db_path = str(tmp_path / "rates.db")
    close_calls = 0
    real_closing = closing

    class ConnWrapper:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def close(self) -> None:
            nonlocal close_calls
            close_calls += 1
            self._conn.close()

        def __enter__(self):
            return self._conn.__enter__()

        def __exit__(self, *args):
            return self._conn.__exit__(*args)

        def __getattr__(self, name: str):
            return getattr(self._conn, name)

    def tracking_closing(conn: sqlite3.Connection):
        return real_closing(ConnWrapper(conn))

    monkeypatch.setattr("finpipe.network.limiter.closing", tracking_closing)

    limiter = AdaptiveRateLimiter("close_test", hard_cap_rps=4.0, db_path=db_path)
    limiter.rate = 2.0
    limiter._save_rate()

    # __init__ opens for schema init, load, and initial save; _save_rate opens once more
    assert close_calls >= 4


def test_adaptive_limiter_no_unclosed_database_warning(tmp_path):
    import warnings

    db_path = str(tmp_path / "rates.db")
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        limiter = AdaptiveRateLimiter("resource_warn", hard_cap_rps=4.0, db_path=db_path)
        limiter.record_429()
