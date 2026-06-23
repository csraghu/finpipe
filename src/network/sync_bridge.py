from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


async def run_sync(func: Callable[..., T], /, *args, **kwargs) -> T:
    """Run blocking vendor code off the event loop (aksh sync_bridge pattern)."""
    return await asyncio.to_thread(func, *args, **kwargs)


async def run_sync_callable(factory: Callable[[], T]) -> T:
    return await asyncio.to_thread(factory)
