"""Default on-disk locations for finpipe state (cache DB, learned rate limits).

Review §6 risk note: SQLite WAL inside an OneDrive-synced tree causes lock
contention and sync churn. Defaults therefore live in the OS-local app-data
directory, NOT the project directory. Both are overridable via config.
"""

from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    """Per-user, non-synced directory for finpipe runtime state."""
    if override := os.getenv("FINPIPE_STATE_DIR"):
        return Path(override)
    if local_appdata := os.getenv("LOCALAPPDATA"):  # Windows
        return Path(local_appdata) / "finpipe"
    if xdg := os.getenv("XDG_CACHE_HOME"):  # Linux
        return Path(xdg) / "finpipe"
    return Path.home() / ".cache" / "finpipe"


def default_cache_db_path() -> str:
    return str(state_dir() / "cache.db")


def default_rate_limit_db_path() -> str:
    return str(state_dir() / "rate_limits.db")
