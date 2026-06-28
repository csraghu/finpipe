"""Live integration tests — one ticker per provider probe (real network).

Requires real API keys in ``.env`` / environment (unit-test mock keys are disabled).

Skips vs failures
-----------------
Some probes are **skipped** (not failed) when the provider responded but returned no
usable data for the probe inputs — e.g. Reddit had no posts for the symbol, or Finviz
parsed zero tickers (often anti-bot HTML). Others skip when a provider is unconfigured
(missing API key).

Configured LLM probes (Groq, Gemini, Nvidia) are **not** skipped on API errors; a skip
on ``llm.nvidia`` usually means ``NVIDIA_API_KEY`` is missing or the NVIDIA API rejected
the request (check the skip message).

Override probe inputs via ``finpipe.settings.json``::

    "health": {
      "probe_symbol": "TSLA",
      "reddit_probe_symbol": "NVDA",
      "finviz_probe_filter": "geo_usa"
    }

Run::

    pytest tests/integration/test_live_provider_probes.py -m live --no-cov -v
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
from finpipe.client import Client
from finpipe.health.models import ProbeResult
from finpipe.health.probes import PROBE_RUNNERS

pytestmark = pytest.mark.live

LIVE_PROBE_SYMBOL = "AAPL"


def _is_skippable_live_failure(result: ProbeResult) -> bool:
    """Treat empty-data / unconfigured probes as skip; surface real API errors."""
    if result.status == "unconfigured":
        return True
    message = (result.message or "").lower()
    if result.status == "degraded" and any(
        token in message
        for token in (
            "no reddit posts",
            "finviz screener returned no tickers",
        )
    ):
        return True
    if result.status == "error" and any(
        token in message
        for token in (
            "401",
            "403",
            "api_key",
            "not configured",
        )
    ):
        return True
    return False


def _skip_if_not_runnable(probe_key: str, result: ProbeResult) -> None:
    if _is_skippable_live_failure(result):
        pytest.skip(f"{probe_key}: {result.message or result.status}")


@pytest.mark.parametrize("probe_key", sorted(PROBE_RUNNERS))
@pytest.mark.asyncio
async def test_live_provider_probe_returns_http_success(probe_key: str):
    """Each enabled provider endpoint must return connected (HTTP 200 semantics)."""
    async with Client() as client:
        if probe_key not in client.health.list_probe_keys():
            pytest.skip(f"{probe_key} not enabled in current config")

        result = await client.health.ping_probe(probe_key)
        _skip_if_not_runnable(probe_key, result)

        health = client.config.health
        symbol_hint = (
            health.reddit_probe_symbol if probe_key == "intel.reddit" else health.probe_symbol
        )
        assert result.http_status == 200, (
            f"{probe_key} failed for {symbol_hint}: "
            f"status={result.status} message={result.message!r} "
            f"latency_ms={result.latency_ms}"
        )
        assert result.ok


@pytest.mark.asyncio
async def test_live_aggregate_health_check():
    """At least one configured probe must succeed; print failures for the rest."""
    async with Client() as client:
        report = await client.health_check()

    connected = [key for key, result in report.results.items() if result.ok]
    skipped = [
        key
        for key, result in report.results.items()
        if result.status in ("skipped", "disabled", "unconfigured")
    ]
    failed = {
        key: result
        for key, result in report.results.items()
        if result.status in ("error", "degraded") and not _is_skippable_live_failure(result)
    }
    skippable = {
        key: result
        for key, result in report.results.items()
        if result.status in ("error", "degraded") and _is_skippable_live_failure(result)
    }

    assert connected, (
        f"No probes connected. Connected={connected} skipped={skipped} "
        f"skippable_failures={ {k: v.message for k, v in skippable.items()} } "
        f"failed={ {k: (v.status, v.message) for k, v in failed.items()} }"
    )

    if failed:
        pytest.fail(
            "Some probes failed: "
            + ", ".join(f"{k}={v.status}({v.message!r})" for k, v in sorted(failed.items()))
        )

    assert report.ok or not failed
