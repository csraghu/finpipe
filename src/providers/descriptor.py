"""Shared helpers for provider ``describe()`` payloads."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_SECRET_KEYS = frozenset({"api_key", "access_key_id", "secret_access_key"})


def redact_secrets(data: Any) -> Any:
    if isinstance(data, dict):
        redacted: dict[str, Any] = {}
        for key, value in data.items():
            if key in _SECRET_KEYS and value:
                redacted[key] = "<configured>"
            else:
                redacted[key] = redact_secrets(value)
        return redacted
    if isinstance(data, list):
        return [redact_secrets(item) for item in data]
    return data


def settings_snapshot(config: BaseModel) -> dict[str, Any]:
    return redact_secrets(config.model_dump())


def provider_descriptor(
    *,
    provider_id: str,
    capability: str | list[str],
    provider_config: BaseModel,
    configured: bool | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable provider descriptor from resolved settings."""
    payload: dict[str, Any] = {
        "provider_id": provider_id,
        "capability": capability,
        "enabled": getattr(provider_config, "enabled", True),
        "settings": settings_snapshot(provider_config),
    }
    if configured is not None:
        payload["configured"] = configured
    if details:
        payload["details"] = details
    return payload
