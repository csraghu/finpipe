from unittest.mock import Mock

import httpx
from finpipe.core.config import CacheConfig
from finpipe.network.resilience import _format_http_status_error, _http_error_body_snippet, _http_error_status, rate_limit_db_path


def test_rate_limit_db_path():
    assert rate_limit_db_path(CacheConfig(cache_type="sqlite", sqlite_db_path="test.db")) == "test.db"

def test_http_error_status():
    resp = httpx.Response(404, request=httpx.Request("GET", "http://test"))
    exc = httpx.HTTPStatusError("error", request=resp.request, response=resp)
    assert _http_error_status(exc) == 404

    mock_exc = Mock()
    mock_exc.response.status_code = 500
    assert _http_error_status(mock_exc) == 500

    assert _http_error_status(ValueError()) is None

def test_http_error_body_snippet():
    mock_exc = Mock()
    mock_exc.response.text = "Error detail"
    assert _http_error_body_snippet(mock_exc) == "Error detail"

    mock_exc.response.text = "x" * 400
    assert len(_http_error_body_snippet(mock_exc)) == 301 # 300 + ellipsis

    mock_exc.response = None
    assert _http_error_body_snippet(mock_exc) == ""

def test_format_http_status_error():
    mock_exc = Mock()
    mock_exc.response.text = "Bad gateway"
    assert _format_http_status_error(502, mock_exc) == "Provider returned error status: 502: Bad gateway"
