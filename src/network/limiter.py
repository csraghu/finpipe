import asyncio
import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from finpipe._internal.aimd import (
    AIMD_ADDITIVE_INCREASE_RPS,
    AIMD_BURST_CAPACITY,
    AIMD_MIN_RATE_RPS,
    AIMD_MULTIPLICATIVE_DECREASE,
    AIMD_SUCCESSES_BEFORE_INCREASE,
    initial_rate_for_cap,
)
from finpipe._internal.limits import get_hard_cap_rps
from finpipe.core.config import RateLimitConfig

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """Async token-bucket limiter with congestion backoff (gemini reference)."""

    def __init__(self, max_rate: float, capacity: float | None = None):
        self._target_max_rate = max_rate
        self._current_max_rate = max_rate
        self._capacity = capacity if capacity is not None else max_rate
        self._tokens = self._capacity
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: float = 1.0) -> None:
        while True:
            async with self._lock:
                self._replenish()
                if self._tokens >= amount:
                    self._tokens -= amount
                    if self._current_max_rate < self._target_max_rate:
                        self._current_max_rate = min(
                            self._target_max_rate, self._current_max_rate * 1.05
                        )
                    return
                tokens_needed = amount - self._tokens
                sleep_time = tokens_needed / self._current_max_rate
            await asyncio.sleep(sleep_time)

    def _replenish(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_update
        tokens_to_add = elapsed * self._current_max_rate
        self._tokens = min(self._capacity, self._tokens + tokens_to_add)
        self._last_update = now

    def trigger_congestion_backoff(self) -> None:
        self._current_max_rate = max(0.1, self._current_max_rate * 0.5)
        logger.warning(
            "Rate limiter congestion backoff triggered",
            extra={"new_rate": self._current_max_rate, "target_rate": self._target_max_rate},
        )


class RpmTpmRateLimiter:
    """Dual-bucket limiter for LLM RPM and TPM caps (concurrent enforcement)."""

    def __init__(self, *, rpm: int | None, tpm: int) -> None:
        self.tpm = tpm
        self.tok_capacity = float(tpm)
        self.tok_tokens = self.tok_capacity
        self.tok_rate = tpm / 60.0
        self.rpm = rpm
        if rpm is not None:
            self.req_capacity = float(rpm)
            self.req_tokens = self.req_capacity
            self.req_rate = rpm / 60.0
        else:
            self.req_capacity = None
            self.req_tokens = None
            self.req_rate = None
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        if (
            self.req_capacity is not None
            and self.req_tokens is not None
            and self.req_rate is not None
        ):
            self.req_tokens = min(self.req_capacity, self.req_tokens + elapsed * self.req_rate)
        self.tok_tokens = min(self.tok_capacity, self.tok_tokens + elapsed * self.tok_rate)
        self.last_refill = now

    async def acquire(self, tokens: int = 1) -> None:
        amount = max(1, tokens)
        while True:
            async with self.lock:
                self._refill()
                req_ok = self.req_tokens is None or self.req_tokens >= 1
                tok_ok = self.tok_tokens >= amount
                if req_ok and tok_ok:
                    if self.req_tokens is not None:
                        self.req_tokens -= 1
                    self.tok_tokens -= amount
                    return
                req_wait = 0.0
                if (
                    self.req_tokens is not None
                    and self.req_rate is not None
                    and self.req_tokens < 1
                ):
                    req_wait = max(0.0, (1 - self.req_tokens) / self.req_rate)
                tok_wait = 0.0
                if self.tok_tokens < amount:
                    tok_wait = max(0.0, (amount - self.tok_tokens) / self.tok_rate)
                wait_time = max(req_wait, tok_wait)
            if wait_time > 0:
                await asyncio.sleep(wait_time)

    async def update_actual_tokens(self, expected: int, actual: int) -> None:
        if actual == expected:
            return
        async with self.lock:
            self._refill()
            diff = expected - actual
            self.tok_tokens = min(self.tok_capacity, self.tok_tokens + diff)


def estimate_llm_token_usage(prompt: str, max_completion_tokens: int) -> int:
    """Conservative pre-request token estimate for TPM bucket acquire."""
    prompt_estimate = max(1, len(prompt) // 4)
    return prompt_estimate + max(1, max_completion_tokens)


class AdaptiveRateLimiter:
    """AIMD token-bucket limiter with SQLite persistence (aksh port; tuning is internal)."""

    def __init__(
        self,
        namespace: str,
        hard_cap_rps: float,
        db_path: str,
    ):
        self.namespace = namespace
        self.db_path = db_path
        self.hard_cap = get_hard_cap_rps(namespace, hard_cap_rps)
        self.min_rate = min(AIMD_MIN_RATE_RPS, self.hard_cap)
        self.capacity = AIMD_BURST_CAPACITY
        self.fallback_max_rate = self.hard_cap
        self.additive_increase = AIMD_ADDITIVE_INCREASE_RPS
        self.multiplicative_decrease = AIMD_MULTIPLICATIVE_DECREASE
        self.successes_before_increase = AIMD_SUCCESSES_BEFORE_INCREASE
        self._init_db()
        default_rate = initial_rate_for_cap(self.hard_cap)
        self.rate = self._clamp_rate(self._load_rate(default_rate))
        self._save_rate()
        self.learned_max = self._clamp_rate(self.rate)
        self.consecutive_successes = 0
        self.tokens = float(self.capacity)
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()
        from finpipe.network.concurrency import DynamicConcurrencyLimiter

        self.concurrency: DynamicConcurrencyLimiter = DynamicConcurrencyLimiter(self)

    def _clamp_rate(self, rate: float) -> float:
        return max(self.min_rate, min(self.hard_cap, rate))

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS api_rate_limits (
                        namespace TEXT PRIMARY KEY,
                        current_rate REAL,
                        last_updated REAL
                    )
                """)

    def _load_rate(self, default_rate: float) -> float:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT current_rate FROM api_rate_limits WHERE namespace = ?",
                (self.namespace,),
            ).fetchone()
            if row:
                return self._clamp_rate(float(row[0]))
        return self._clamp_rate(default_rate)

    def _save_rate(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO api_rate_limits (namespace, current_rate, last_updated)
                    VALUES (?, ?, ?)
                    ON CONFLICT(namespace) DO UPDATE SET
                        current_rate=excluded.current_rate,
                        last_updated=excluded.last_updated
                    """,
                    (self.namespace, self.rate, time.time()),
                )

    async def acquire(self) -> None:
        wait_time = 0.0
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
            else:
                wait_time = (1.0 - self.tokens) / self.rate if self.rate > 0 else 0.0
                self.tokens = 0.0
                self.last_refill = now + wait_time
        if wait_time > 0:
            await asyncio.sleep(wait_time)

    def record_success(self) -> None:
        self.consecutive_successes += 1
        if self.rate < self.learned_max or (
            self.consecutive_successes >= self.successes_before_increase
            and self.rate < self.hard_cap
        ):
            old_rate = self.rate
            self.rate = self._clamp_rate(self.rate + self.additive_increase)
            if self.rate != old_rate:
                self._save_rate()
            if self.consecutive_successes >= self.successes_before_increase:
                self.consecutive_successes = 0

    def record_429(self) -> None:
        self.consecutive_successes = 0
        old_rate = self.rate
        self.rate = self._clamp_rate(max(self.min_rate, self.rate * self.multiplicative_decrease))
        if self.rate != old_rate:
            self._save_rate()


def build_adaptive_limiter(
    namespace: str,
    config: RateLimitConfig,
    db_path: str | None = None,
) -> AdaptiveRateLimiter:
    from finpipe.network.resilience import rate_limit_db_path

    resolved_db = db_path or rate_limit_db_path(None)
    return AdaptiveRateLimiter(
        namespace=namespace,
        hard_cap_rps=config.max_requests_per_second,
        db_path=resolved_db,
    )
