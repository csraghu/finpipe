from finpipe.health.models import HealthReport, ProbeResult


def test_probe_result_to_dict_includes_optional_fields():
    result = ProbeResult("equity.yahoo", "connected", message="ok", latency_ms=12.3456)
    payload = result.to_dict()
    assert payload == {
        "key": "equity.yahoo",
        "status": "connected",
        "message": "ok",
        "latency_ms": 12.35,
    }


def test_probe_result_to_dict_omits_none_fields():
    result = ProbeResult("equity.yahoo", "skipped")
    assert result.to_dict() == {"key": "equity.yahoo", "status": "skipped"}


def test_health_report_properties_and_to_dict():
    report = HealthReport(
        results={
            "a": ProbeResult("a", "connected"),
            "b": ProbeResult("b", "degraded", message="slow"),
        }
    )
    assert report.all_connected is False
    assert report.has_errors is True
    payload = report.to_dict()
    assert payload["all_connected"] is False
    assert payload["has_errors"] is True
    assert set(payload["probes"]) == {"a", "b"}
