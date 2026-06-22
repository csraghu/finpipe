"""Internal AIMD tuning constants (not user-configurable)."""

from __future__ import annotations

# aksh defaults — hard cap comes from finpipe.settings.json only
AIMD_MIN_RATE_RPS: float = 0.1
AIMD_DEFAULT_INITIAL_RATE_RPS: float = 1.0
AIMD_BURST_CAPACITY: int = 10
AIMD_ADDITIVE_INCREASE_RPS: float = 0.5
AIMD_MULTIPLICATIVE_DECREASE: float = 0.75
AIMD_SUCCESSES_BEFORE_INCREASE: int = 50

DEFAULT_RATE_LIMIT_DB_PATH: str = ".cache/finpipe/rate_limits.db"


def initial_rate_for_cap(hard_cap_rps: float) -> float:
    """Conservative starting rate when no learned rate exists in the DB."""
    return min(hard_cap_rps, max(AIMD_MIN_RATE_RPS, AIMD_DEFAULT_INITIAL_RATE_RPS))
