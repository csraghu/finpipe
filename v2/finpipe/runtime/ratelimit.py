"""Rate limiting: AIMD adaptive limiter, RPM bucket, RPM+TPM dual bucket, concurrency.

Port of the v1 algorithms (the review found them sound) with three fixes:
- persistence is DEBOUNCED and runs via ``asyncio.to_thread`` — no SQLite write on
  the event loop for every ±0.5 RPS change (review §4 "blocking I/O in async paths")
- hard-cap lookup logs a one-time warning when a namespace has no documented cap,
  instead of silently skipping the clamp (review §4 "namespace mismatches")
- sub-source namespaces fall back to their leaf name (``sentiment.reddit`` → ``reddit``)
  so the documented caps actually apply.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .paths import default_rate_limit_db_path

logger = logging.getLogger(__name__)

# --- AIMD tuning (internal, not user-configurable) ---------------------------
AIMD_MIN_RATE_RPS = 0.1
AIMD_DEFAULT_INITIAL_RATE_RPS = 1.0
AIMD_BURST_CAPACITY = 10
AIMD_ADDITIVE_INCREASE_RPS = 0.5
AIMD_MULTIPLICATIVE_DECREASE = 0.75
AIMD_SUCCESSES_BEFORE_INCREASE = 50
PERSIST_DEBOUNCE_SEC = 5.0

# --- Documented vendor ceilings (clamp user config; never exceed) -------------
HARD_LIMITS_RPS: dict[str, float] = {
    "yahoo": 2.0,
    "alpha_vantage": 0.083,
    "fred": 2.0,
    "massive": 5.0,
    "groq": 30 / 60.0,
    "gemini": 60 / 60.0,
    "nvidia": 60 / 60.0,
    "stocktwits": 60 / 60.0,
    "google_news": 1.0,
    "reddit": 0.5,
    "tradingview": 2.0,
    "yahoo_trending": 2.0,
    "yahoo_predefined": 2.0,
    "finviz": 2.0,
}

_warned_namespaces: set[str] = set()


def hard_cap_rps(namespace: str, configured: float) -> float:
    """Clamp to the documented ceiling; leaf-name fallback; warn once on miss."""
    limit = HARD_LIMITS_RPS.get(namespace)
    if limit is None and "." in namespace:
        limit = HARD_LIMITS_RPS.get(namespace.rsplit(".", 1)[-1])
    if limit is None:
        if namespace not in _warned_namespaces:
            _warned_namespaces.add(namespace)
            logger.warning(
                "No documented hard cap for namespace %r; using configured %.3f rps",
                namespace, configured,
            )
        return configured
    if configured > limit:
        logger.warning("Clamping %s rate %.3f → documented cap %.3f rps", namespace, configured, limit)
    return min(configured, limit)


class AdaptiveRateLimiter:
    """AIMD token bucket with debounced SQLite persistence of the learned rate."""

    def __init__(self, namespace: str, configured_rps: float, db_path: str | None = None) -> None:
        self.namespace = namespace
        self.db_path = db_path or default_rate_limit_db_path()
        self.hard_cap = hard_cap_rps(namespace, configured_rps)
        self.min_rate = min(AIMD_MIN_RATE_RPS, self.hard_cap)
        self.capacity = float(AIMD_BURST_CAPACITY)
        self._init_db()
        initial = min(self.hard_cap, max(AIMD_MIN_RATE_RPS, AIMD_DEFAULT_INITIAL_RATE_RPS))
        self.rate = self._clamp(self._load_rate(initial))
        self.learned_max = self.rate
        self.consecutive_successes = 0
        self.tokens = self.capacity
        self.last_refill = time.monotonic()
        self.lock = asyncio.Lock()
        self._dirty = False
        self._last_persist = 0.0
        self.concurrency = DynamicConcurrencyLimiter(self)

    # -- persistence (sync helpers, called off-loop) ---------------------------
    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS api_rate_limits (
                       namespace TEXT PRIMARY KEY, current_rate REAL, last_updated REAL)"""
            )

    def _load_rate(self, default: float) -> float:
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT current_rate FROM api_rate_limits WHERE namespace = ?", (self.namespace,)
            ).fetchone()
        return float(row[0]) if row else default

    def _persist_now(self) -> None:
        with closing(sqlite3.connect(self.db_path)) as conn, conn:
            conn.execute(
                """INSERT INTO api_rate_limits (namespace, current_rate, last_updated)
                   VALUES (?, ?, ?)
                   ON CONFLICT(namespace) DO UPDATE SET
                       current_rate=excluded.current_rate, last_updated=excluded.last_updated""",
                (self.namespace, self.rate, time.time()),
            )
        self._dirty = False
        self._last_persist = time.monotonic()

    async def _maybe_persist(self) -> None:
        if self._dirty and (time.monotonic() - self._last_persist) >= PERSIST_DEBOUNCE_SEC:
            await asyncio.to_thread(self._persist_now)

    async def flush(self) -> None:
        """Persist any pending rate change (call from close())."""
        if self._dirty:
            await asyncio.to_thread(self._persist_now)

    # -- AIMD ------------------------------------------------------------------
    def _clamp(self, rate: float) -> float:
        return max(self.min_rate, min(self.hard_cap, rate))

    async def acquire(self) -> None:
        wait = 0.0
        async with self.lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last_refill) * self.rate)
            self.last_refill = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
            else:
                wait = (1.0 - self.tokens) / self.rate if self.rate > 0 else 0.0
                self.tokens = 0.0
                self.last_refill = now + wait
        await self._maybe_persist()
        if wait > 0:
            await asyncio.sleep(wait)

    def record_success(self) -> None:
        self.consecutive_successes += 1
        below_learned = self.rate < self.learned_max
        streak_reached = self.consecutive_successes >= AIMD_SUCCESSES_BEFORE_INCREASE
        if below_learned or (streak_reached and self.rate < self.hard_cap):
            new_rate = self._clamp(self.rate + AIMD_ADDITIVE_INCREASE_RPS)
            if new_rate != self.rate:
                self.rate = new_rate
                self._dirty = True
            if streak_reached:
                self.consecutive_successes = 0

    def record_429(self) -> None:
        self.consecutive_successes = 0
        new_rate = self._clamp(self.rate * AIMD_MULTIPLICATIVE_DECREASE)
        if new_rate != self.rate:
            self.rate = new_rate
            self._dirty = True


class DynamicConcurrencyLimiter:
    """Caps in-flight requests at max(1, current_rate × multiplier)."""

    def __init__(self, limiter: AdaptiveRateLimiter, latency_multiplier: float = 1.0) -> None:
        self._limiter = limiter
        self._multiplier = latency_multiplier
        self._active = 0
        self._condition = asyncio.Condition()

    def _limit(self) -> int:
        return max(1, int(self._limiter.rate * self._multiplier))

    @contextlib.asynccontextmanager
    async def limit(self):
        async with self._condition:
            while self._active >= self._limit():
                await self._condition.wait()
            self._active += 1
        try:
            yield
        finally:
            async with self._condition:
                self._active -= 1
                self._condition.notify_all()


class TokenBucket:
    """Simple async token bucket (used for RPM-only caps)."""

    def __init__(self, rate_per_sec: float, capacity: float) -> None:
        self._rate = rate_per_sec
        self._capacity = capacity
        self._tokens = capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, amount: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= amount:
                    self._tokens -= amount
                    return
                wait = (amount - self._tokens) / self._rate
            await asyncio.sleep(wait)


class RpmTpmLimiter:
    """Dual bucket: requests/minute AND tokens/minute enforced concurrently (LLMs)."""

    def __init__(self, *, rpm: int | None, tpm: int) -> None:
        self._req = TokenBucket(rpm / 60.0, float(rpm)) if rpm is not None else None
        self._tok = TokenBucket(tpm / 60.0, float(tpm))

    async def acquire(self, tokens: int = 1) -> None:
        if self._req is not None:
            await self._req.acquire(1.0)
        await self._tok.acquire(float(max(1, tokens)))

    async def refund(self, expected: int, actual: int) -> None:
        """Return over-estimated tokens to the bucket after usage metadata arrives."""
        diff = expected - actual
        if diff <= 0:
            return
        async with self._tok._lock:
            self._tok._tokens = min(self._tok._capacity, self._tok._tokens + diff)


def estimate_llm_token_usage(prompt: str, max_completion_tokens: int) -> int:
    """Conservative pre-request estimate for the TPM bucket."""
    return max(1, len(prompt) // 4) + max(1, max_completion_tokens)
