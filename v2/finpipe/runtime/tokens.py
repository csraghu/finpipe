"""In-memory token store for OAuth/bearer tokens.

Review §3: v1 persisted Schwab/Reddit tokens into the on-disk fetch cache in
plaintext. Tokens are short-lived credentials — they live in process memory only.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class TokenStore:
    def __init__(self) -> None:
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = asyncio.Lock()

    async def get_or_fetch(
        self,
        key: str,
        fetch: Callable[[], Awaitable[tuple[str, float]]],
        *,
        expiry_buffer_sec: float = 60.0,
    ) -> str:
        """Return a live token; call ``fetch`` → (token, ttl_seconds) when absent/expired."""
        async with self._lock:
            entry = self._tokens.get(key)
            if entry is not None and time.monotonic() < entry[1]:
                return entry[0]
            token, ttl = await fetch()
            self._tokens[key] = (token, time.monotonic() + max(1.0, ttl - expiry_buffer_sec))
            return token

    def invalidate(self, key: str) -> None:
        self._tokens.pop(key, None)
