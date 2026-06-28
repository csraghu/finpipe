from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

_conftest_path = Path(__file__).with_name("conftest.py")
_spec = importlib.util.spec_from_file_location("integration_conftest", _conftest_path)
assert _spec and _spec.loader
_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_conftest)
live_tests_enabled = _conftest.live_tests_enabled
_markexpr_requests_live = _conftest._markexpr_requests_live


def test_live_tests_enabled_from_run_live_flag():
    config = MagicMock()
    config.getoption.side_effect = lambda name, default=False: name == "--run-live"
    assert live_tests_enabled(config) is True


def test_live_tests_enabled_from_markexpr_live():
    config = MagicMock()
    config.getoption.side_effect = lambda name, default=False: (
        "live" if name == "markexpr" else default
    )
    assert _markexpr_requests_live(config) is True
    assert live_tests_enabled(config) is True


def test_live_tests_enabled_from_env(monkeypatch):
    monkeypatch.setenv("FINPIPE_RUN_LIVE_TESTS", "1")
    config = MagicMock()
    config.getoption.return_value = False
    assert live_tests_enabled(config) is True
