"""Fixtures for live network integration tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_LIVE_ENV_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _load_dotenv() -> None:
    """Load repo .env into os.environ (same discovery as export script)."""
    current = Path.cwd().resolve()
    for env_path in (current / ".env", current.parent / ".env"):
        if not env_path.is_file():
            continue
        with open(env_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = [part.strip() for part in line.split("=", 1)]
                if val.startswith(('"', "'")) and val.endswith(('"', "'")) and len(val) >= 2:
                    val = val[1:-1]
                os.environ.setdefault(key, val)
        return


def _markexpr_requests_live(config: pytest.Config) -> bool:
    markexpr = (config.getoption("markexpr") or "").strip()
    if not markexpr:
        return False
    if markexpr == "live":
        return True
    # e.g. "live and not slow" — require live token as a whole word
    return any(token == "live" for token in markexpr.replace("(", " ").replace(")", " ").split())


def live_tests_enabled(config: pytest.Config) -> bool:
    if config.getoption("--run-live"):
        return True
    if _markexpr_requests_live(config):
        return True
    value = os.environ.get("FINPIPE_RUN_LIVE_TESTS", "").strip().lower()
    return value in _LIVE_ENV_TRUTHY


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run live provider probe tests (real network; alternative to FINPIPE_RUN_LIVE_TESTS=1)",
    )


@pytest.fixture(autouse=True)
def _require_live_opt_in(request: pytest.FixtureRequest):
    if request.node.get_closest_marker("live") is None:
        return
    if not live_tests_enabled(request.config):
        pytest.skip(
            "Live probes disabled. Run: pytest tests/integration/ -m live"
            "  | pytest ... --run-live"
            "  | bash: export FINPIPE_RUN_LIVE_TESTS=1"
        )
    _load_dotenv()
