# finpipe

`finpipe` is a robust, strictly-typed, resilient financial data pipeline designed to seamlessly fetch data from multiple providers (Yahoo Finance, FRED, Alpha Vantage, Massive Options, etc.) while shielding downstream applications from rate limits, network timeouts, and bad data formatting.

## Architecture Highlights
* **Composite capability APIs:** `client.equity`, `client.options`, and `client.intel` route across configured primary/fallback providers.
* **Native Polars Support:** All time-series data is returned as high-performance `polars.DataFrame`.
* **Strict Pydantic Models:** All structured data (Metadata, Options, Sentiment) is returned as strict Pydantic models.
* **Steady-State Rate Limiting:** Asynchronous Token Bucket rate limiting ensures API calls are metered smoothly without crashing or violating quotas.
* **Resilience:** Circuit Breakers and Tenacity retries (with exponential backoff and jitter) are baked directly into the core engine.
* **Encapsulated Caching:** `finpipe` transparently supports InMemory TTL caching and SQLite caching.

## Environment Variables Configuration

`finpipe` is designed to be completely driven by environment variables out-of-the-box. The application must set the following environment variables if it intends to use their respective APIs.

Before execution of any specific provider's API, `finpipe` actively checks if the required variables are loaded and will safely raise a `FinpipeConfigError` if they are missing.

### Provider API Keys
* `ALPHA_VANTAGE_API_KEY`: API key for fetching Alpha Vantage equity data.
* `FRED_API_KEY`: API key for the Federal Reserve Economic Data endpoints.
* `GROQ_API_KEY`: API key for LLM generation via Groq.
* `GEMINI_API_KEY`: API key for LLM generation via Google Gemini.

### Massive Options Configuration
Massive Options requires specific S3 storage and API configurations:
* `MASSIVE_API_KEY`: The main API token.
* `MASSIVE_ACCESS_KEY_ID`: S3 bucket access key.
* `MASSIVE_SECRET_ACCESS_KEY`: S3 bucket secret key.
* `MASSIVE_S3_ENDPOINT`: S3 bucket endpoint (e.g., `https://files.massive.com`).
* `MASSIVE_S3_BUCKET`: The target bucket (e.g., `flatfiles`).

## Quick Start

See **[docs/api-reference.md](docs/api-reference.md)** for installation, `finpipe.settings.json`, environment variables, and the full public API.

Minimal example (async-only):

```python
import asyncio
from datetime import date, timedelta

from finpipe import Client, FinpipeConfig

async def main() -> None:
    config = FinpipeConfig.load()  # discovers ./finpipe.settings.json
    async with Client(config) as client:
        end = date.today()
        start = end - timedelta(days=365)
        df = await client.fred.get_macro_series("GDP", start, end)
        print(len(df))

asyncio.run(main())
```

Set `FRED_API_KEY` (and other provider keys) in the environment before running — see the API reference.

## Development

Install dev dependencies and run the full quality gate before committing:

```powershell
pip install -e ".[dev,httpx,yahoo,fred,massive,sentiment]"
pre-commit install
.\scripts\run_checks.ps1
```

See **[docs/api-reference.md](docs/api-reference.md#development--quality-gates)** and **[docs/architecture.md](docs/architecture.md#development-workflow-and-quality-gates)** for type-check setup (`typecheck/finpipe` junction), hooks, and coverage policy.
