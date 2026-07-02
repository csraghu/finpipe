"""v2 test fixtures.

Deliberately does NOT set any provider API keys: constructing ``Client()`` and
using unrelated providers must work without them (that is itself under test —
review §2.4). Individual tests set the one secret they need.
"""

from __future__ import annotations

from typing import Any

import pytest

_SECRET_ENV_VARS = (
    "FRED_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
    "MASSIVE_API_KEY",
    "MASSIVE_ACCESS_KEY_ID",
    "MASSIVE_SECRET_ACCESS_KEY",
    "MASSIVE_S3_ENDPOINT",
    "MASSIVE_S3_BUCKET",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "FINPIPE_CONFIG",
    "FINPIPE_CACHE_BACKEND",
)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Temp sqlite state, singleton reset, and a credential-free environment.

    Deleting the secret env vars makes 'zero-credential Client' and
    'missing-key raises on first use' deterministic regardless of the
    developer machine's real environment.
    """
    monkeypatch.setenv("FINPIPE_STATE_DIR", str(tmp_path / "state"))
    for name in _SECRET_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    from finpipe.runtime.cache import CacheManager

    CacheManager.reset()
    yield
    CacheManager.reset()


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text or (str(json_data) if json_data is not None else "")

    def json(self) -> Any:
        return self._json


class FakeTransport:
    """Queued-response transport for RequestExecutor tests."""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict]] = []
        self.closed = False

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("FakeTransport exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


class FakeExecutor:
    """Adapter-facing executor stub mirroring RequestExecutor's public surface."""

    def __init__(self, responses: list[FakeResponse | Exception] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str, dict]] = []
        self.rate_limited_notes = 0
        self.reconciled: list[tuple[int, int]] = []
        self.closed = False

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("FakeExecutor exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def execute(self, operation, *, context: str = "") -> Any:
        return await operation()

    def note_rate_limited(self) -> None:
        self.rate_limited_notes += 1

    async def reconcile_token_usage(self, expected: int, actual: int) -> None:
        self.reconciled.append((expected, actual))

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_response():
    return FakeResponse


@pytest.fixture
def fake_transport():
    return FakeTransport


@pytest.fixture
def fake_executor():
    return FakeExecutor


def make_runtime(config: Any, executor: Any, *, provider_key: str = "test", strict: bool = True):
    """Build a narrow ProviderRuntime around a strict in-memory cache."""
    from finpipe.providers.base import ProviderRuntime
    from finpipe.runtime.cache import MemoryCache, NamespacedCache

    def executor_factory(namespace: str, rate_limits: Any, http: Any) -> Any:
        return executor

    return ProviderRuntime(
        config=config,
        cache=NamespacedCache(MemoryCache(strict=strict), "test-app", provider_key),
        executor=executor,
        dataframe_format="polars",
        executor_factory=executor_factory,
        llm_prompt=None,
    )
