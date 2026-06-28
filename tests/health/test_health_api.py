from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from finpipe.health import run_health_check, run_probe
from finpipe.health.models import HealthReport, ProbeResult


def test_probe_result_http_status_mapping():
    assert ProbeResult("equity.yahoo", "connected").http_status == 200
    assert ProbeResult("equity.yahoo", "connected").ok is True
    assert ProbeResult("equity.yahoo", "degraded", message="empty").http_status == 503
    assert ProbeResult("equity.yahoo", "error", message="down").http_status == 503
    assert ProbeResult("equity.yahoo", "unconfigured").http_status == 501
    assert ProbeResult("equity.yahoo", "skipped").http_status == 204


def test_probe_result_to_dict_includes_http_fields():
    payload = ProbeResult("intel.stocktwits", "connected", latency_ms=12.345).to_dict()
    assert payload == {
        "key": "intel.stocktwits",
        "status": "connected",
        "ok": True,
        "http_status": 200,
        "latency_ms": 12.35,
    }


def test_health_report_ok_and_http_status():
    report = HealthReport(
        results={
            "equity.yahoo": ProbeResult("equity.yahoo", "connected"),
            "intel.reddit": ProbeResult("intel.reddit", "degraded", message="empty"),
        }
    )
    assert report.ok is False
    assert report.http_status == 503

    ok_report = HealthReport(
        results={
            "equity.yahoo": ProbeResult("equity.yahoo", "connected"),
            "llm.groq": ProbeResult("llm.groq", "unconfigured", message="no key"),
        }
    )
    assert ok_report.ok is True
    assert ok_report.http_status == 200


def test_health_report_to_dict_includes_aggregate_http_status():
    report = HealthReport(results={"equity.yahoo": ProbeResult("equity.yahoo", "connected")})
    payload = report.to_dict()
    assert payload["ok"] is True
    assert payload["http_status"] == 200
    assert "probes" in payload


@pytest.mark.asyncio
async def test_run_health_check_delegates_to_client():
    mock_report = HealthReport(results={"equity.yahoo": ProbeResult("equity.yahoo", "connected")})
    mock_health = MagicMock()
    mock_health.ping = AsyncMock(return_value=mock_report)
    mock_client = MagicMock()
    mock_client.health = mock_health
    mock_client.close = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("finpipe.client.Client", return_value=mock_client):
        report = await run_health_check()

    assert report.ok
    mock_health.ping.assert_awaited_once()
    mock_client.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_probe_single_key():
    mock_result = ProbeResult("options.yahoo", "connected")
    mock_health = MagicMock()
    mock_health.ping_probe = AsyncMock(return_value=mock_result)
    mock_client = MagicMock()
    mock_client.health = mock_health
    mock_client.close = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("finpipe.client.Client", return_value=mock_client):
        result = await run_probe("options.yahoo")

    assert result.ok
    mock_health.ping_probe.assert_awaited_once_with("options.yahoo")
