from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from finpipe.network.limiter import AdaptiveRateLimiter


class DynamicConcurrencyLimiter:
    """Bounds concurrent requests based on adaptive rate (aksh port)."""

    def __init__(self, rate_limiter: AdaptiveRateLimiter, latency_multiplier: float = 1.0):
        self.rate_limiter = rate_limiter
        self.latency_multiplier = latency_multiplier
        self.active_tasks = 0
        self.condition = asyncio.Condition()

    def _get_dynamic_limit(self) -> int:
        return max(1, int(self.rate_limiter.rate * self.latency_multiplier))

    async def acquire(self) -> None:
        async with self.condition:
            while self.active_tasks >= self._get_dynamic_limit():
                await self.condition.wait()
            self.active_tasks += 1

    async def release(self) -> None:
        async with self.condition:
            self.active_tasks -= 1
            self.condition.notify_all()

    @contextlib.asynccontextmanager
    async def limit(self):
        await self.acquire()
        try:
            yield
        finally:
            await self.release()
