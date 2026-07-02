"""Finpipe error hierarchy (v1-compatible, plus FinpipeAuthError).

Classification happens ONLY in runtime/resilience.py. Adapters may add context
to a classified error; they must never convert one class into another
(v1's Massive adapter turned rate-limit errors into DataNotFound — see review §4).
"""

from __future__ import annotations


class FinpipeError(Exception):
    """Base exception for all finpipe errors."""


class FinpipeConfigError(FinpipeError):
    """Configuration is missing or invalid (raised on first use, not at Client())."""


class FinpipeAuthError(FinpipeConfigError):
    """Provider rejected credentials (401/403). Never retried, never falls back."""


class FinpipeDataNotFoundError(FinpipeError):
    """Requested resource does not exist at this provider. Never retried; may fall back."""


class FinpipeRateLimitExceededError(FinpipeError):
    """Throttled after bounded retries. Never triggers fallback (see capabilities/policy.py)."""


class FinpipeProviderDownError(FinpipeError):
    """Provider unresponsive / 5xx / network failure after retries. May fall back."""


class FinpipeParseError(FinpipeError):
    """Provider payload could not be parsed into the canonical schema."""
