"""Provider describe() payload helpers — redaction delegated to core.redact (single impl)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..core.redact import redact


def settings_snapshot(config: BaseModel) -> dict[str, Any]:
    return redact(config.model_dump())


def provider_descriptor(
    provider_id: str,
    capability: str | list[str],
    provider_config: BaseModel,
    *,
    configured: bool | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
