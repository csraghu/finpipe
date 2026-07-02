"""Canonical, strict serialization for cache payloads.

Fixes review §2.2: the v1 SQLite cache silently dropped every payload containing
``datetime``/``pd.Timestamp``/DataFrames because ``json.dumps`` raised inside a
swallowed ``except``. Here every supported type round-trips exactly, and anything
unsupported raises ``CodecError`` loudly instead of caching nothing.

Envelope format (versioned so the schema can evolve):

    {"v": 1, "t": "<tag>", "d": <payload>}

Supported tags: ``json`` (plain data), ``dt`` (datetime), ``date``, ``pl-df``
(polars DataFrame as base64 parquet), ``pd-df`` (pandas DataFrame as base64 parquet).
Nested datetimes/dates inside dicts/lists are handled recursively.
"""

from __future__ import annotations

import base64
import io
import json
import math
from datetime import date, datetime
from typing import Any

import pandas as pd
import polars as pl

_VERSION = 1


class CodecError(TypeError):
    """Raised when a value cannot be canonically serialized. Never swallow this."""


def _encode_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _encode_scalar(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_scalar(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return {"v": _VERSION, "t": "dt", "d": value.isoformat()}
    if isinstance(value, datetime):
        return {"v": _VERSION, "t": "dt", "d": value.isoformat()}
    if isinstance(value, date):
        return {"v": _VERSION, "t": "date", "d": value.isoformat()}
    if isinstance(value, float):
        # NaN/inf from pandas records → None (JSON has no NaN; allow_nan=False)
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    # numpy scalars (from .to_dict(orient="records")) → native python
    item = getattr(value, "item", None)
    if callable(item):
        return _encode_scalar(item())
    raise CodecError(f"Unsupported cache value type: {type(value).__name__}")


def _decode_scalar(value: Any) -> Any:
    if isinstance(value, dict):
        tag = value.get("t") if value.get("v") == _VERSION else None
        if tag == "dt":
            return datetime.fromisoformat(value["d"])
        if tag == "date":
            return date.fromisoformat(value["d"])
        return {k: _decode_scalar(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_scalar(v) for v in value]
    return value


def dumps(value: Any) -> str:
    """Serialize a cacheable value to a canonical JSON string.

    Raises ``CodecError`` for unsupported types — callers must not swallow it.
    """
    if isinstance(value, pl.DataFrame):
        buf = io.BytesIO()
        value.write_parquet(buf)
        payload = {"v": _VERSION, "t": "pl-df", "d": base64.b64encode(buf.getvalue()).decode()}
    elif isinstance(value, pd.DataFrame):
        buf = io.BytesIO()
        value.to_parquet(buf, index=False)
        payload = {"v": _VERSION, "t": "pd-df", "d": base64.b64encode(buf.getvalue()).decode()}
    else:
        payload = {"v": _VERSION, "t": "json", "d": _encode_scalar(value)}
    try:
        return json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CodecError(f"Value not canonically serializable: {exc}") from exc


def loads(raw: str) -> Any:
    envelope = json.loads(raw)
    tag = envelope.get("t")
    data = envelope.get("d")
    if tag == "pl-df":
        return pl.read_parquet(io.BytesIO(base64.b64decode(data)))
    if tag == "pd-df":
        return pd.read_parquet(io.BytesIO(base64.b64decode(data)))
    if tag == "json":
        return _decode_scalar(data)
    raise CodecError(f"Unknown cache envelope tag: {tag!r}")


def digest_key(*parts: Any) -> str:
    """Stable cache-key digest (fixes review §2.5 — no salted ``hash()``)."""
    import hashlib

    canonical = json.dumps(
        [_encode_scalar(p) if not isinstance(p, (str, int, float, bool, type(None))) else p for p in parts],
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
