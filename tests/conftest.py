import pytest

from finpipe.core.config import FinpipeConfig


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """Set mock environment variables for testing."""
    monkeypatch.setenv("MASSIVE_API_KEY", "test_massive")
    monkeypatch.setenv("MASSIVE_ACCESS_KEY_ID", "test_id")
    monkeypatch.setenv("MASSIVE_SECRET_ACCESS_KEY", "test_secret")
    monkeypatch.setenv("MASSIVE_S3_ENDPOINT", "http://test")
    monkeypatch.setenv("MASSIVE_S3_BUCKET", "test_bucket")
    monkeypatch.setenv("FRED_API_KEY", "test_fred")
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini")
    monkeypatch.setenv("GROQ_API_KEY", "test_groq")
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "test_av")
    monkeypatch.setenv("FINPIPE_CACHE_BACKEND", "memory")


@pytest.fixture
def config():
    """Return a base FinpipeConfig for tests."""
    return FinpipeConfig()


@pytest.fixture
def pandas_config():
    """FinpipeConfig that avoids polars conversion in dataframe helpers."""
    return FinpipeConfig(dataframe_format="pandas")
