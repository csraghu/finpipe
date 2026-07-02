"""The ONE redaction implementation (review §3: v1 had two copies, both incomplete).

Strategy — defense in depth:
1. Credentials are declared as ``pydantic.SecretStr`` in config models, so
   ``model_dump()`` already renders them as ``'**********'``.
2. This module additionally scrubs by key suffix, so a plain-string secret added
   by mistake is still caught in every dump/describe payload.

A contract test iterates every dump/describe surface and asserts no configured
secret *value* appears anywhere in the output.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr

_REDACTED = "<redacted>"
_SECRET_SUFFIXES = ("_key", "_secret", "_token", "_password", "_credential")
_SECRET_EXACT = frozenset({"api_key", "apikey", "client_id", "access_key_id"})


def is_secret_field(name: str) -> bool:
    lowered = name.lower()
    return lowered in _SECRET_EXACT or lowered.endswith(_SECRET_SUFFIXES)


def redact(data: Any) -> Any:
    """Recursively redact secret-looking fields and SecretStr values."""
    if isinstance(data, SecretStr):
        return _REDACTED
    if isinstance(data, dict):
        return {
            key: (_REDACTED if is_secret_field(str(key)) and value else redact(value))
            for key, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [redact(item) for item in data]
    return data
