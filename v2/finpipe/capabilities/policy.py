"""Fallback policy as data — the ONLY fallback logic in the package.

Implements the rules architecture.md documented but v1 never enforced (review §4):
- fall back on: data-not-found, provider-down, or an empty primary result
- NEVER fall back on: rate-limit (don't hammer the fallback during throttling),
  auth/config errors (a bad key won't get better on another vendor's data).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from ..core.errors import (
    FinpipeConfigError,
    FinpipeDataNotFoundError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

FALLBACK_ON: tuple[type[Exception], ...] = (FinpipeDataNotFoundError, FinpipeProviderDownError)
NEVER_FALLBACK_ON: tuple[type[Exception], ...] = (FinpipeRateLimitExceededError, FinpipeConfigError)


def is_empty(result: Any) -> bool:
    """Empty-result detection across canonical return types."""
    if result is None:
        return True
    if isinstance(result, (list, dict, str)):
        return len(result) == 0
    is_empty_attr = getattr(result, "is_empty", None)  # polars
    if callable(is_empty_attr):
        return bool(is_empty_attr())
    empty_attr = getattr(result, "empty", None)  # pandas
    if isinstance(empty_attr, bool):
        return empty_attr
    calls = getattr(result, "calls", None)  # OptionChain
    puts = getattr(result, "puts", None)
    if calls is not None and puts is not None:
        return not calls and not puts
    return False


async def call_with_fallback(
    label: str,
    attempts: list[tuple[str, Callable[[], Awaitable[T]]]],
) -> T:
    """Run ordered (provider_name, coroutine factory) attempts under the policy."""
    last_error: Exception | None = None
    last_result: T | None = None
    got_result = False

    for name, attempt in attempts:
        try:
            result = await attempt()
        except NEVER_FALLBACK_ON:
            raise
        except FALLBACK_ON as exc:
            last_error = exc
            logger.warning("%s: provider %s failed (%s); trying fallback", label, name, type(exc).__name__)
            continue
        if is_empty(result):
            last_result, got_result = result, True
            logger.info("%s: provider %s returned empty; trying fallback", label, name)
            continue
        return result

    if got_result:
        assert last_result is not None
        return last_result  # all empty — return the (empty) canonical result, not an error
    if last_error is not None:
        raise last_error
    raise FinpipeProviderDownError(f"{label}: no providers configured")
