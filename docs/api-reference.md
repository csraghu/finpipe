# finpipe API reference & application guide

This document is the **application-facing reference** for finpipe: installation, configuration, secrets, and the public API surface as implemented in v0.5.0.

For internal design (rate limiting, transport choices, migration from aksh), see [architecture.md](./architecture.md).

---

## Table of contents

1. [Quick start](#quick-start)
2. [Installation & dependencies](#installation--dependencies)
3. [Configuration (`finpipe.settings.json`)](#configuration-finpipesettingsjson)
4. [Environment variables](#environment-variables)
5. [Using `Client`](#using-client)
6. [Package exports](#package-exports)
7. [Capability protocols (target API)](#capability-protocols-target-api)
8. [Provider adapters (current API)](#provider-adapters-current-api)
9. [Return types & schemas](#return-types--schemas)
10. [Exceptions](#exceptions)
11. [Inspecting resolved settings](#inspecting-resolved-settings)
12. [Development & quality gates](#development--quality-gates)

---

## Quick start

### 1. Install finpipe

```bash
pip install -e ".[httpx,yahoo]"   # development / local checkout
# or, for all optional provider deps:
pip install -e ".[httpx,yahoo,fred,massive,sentiment]"
```

Python **3.12+** is required.

### 2. Copy settings

```bash
cp docs/finpipe.settings.example.json finpipe.settings.json
```

Edit rate limits, TTLs, routing, and cache paths. **Do not put API keys in this file** — use environment variables (see below).

### 3. Set secrets in the environment

```bash
# Linux / macOS
export FRED_API_KEY="your-key"
export ALPHA_VANTAGE_API_KEY="your-key"
# … see full table below

# Windows PowerShell
$env:FRED_API_KEY = "your-key"
```

Or load a `.env` file from your application before creating `Client` (finpipe does not load `.env` automatically).

### 4. Run async code

All provider I/O is **async-only**. Use `async with Client()` and `await` every fetch:

```python
import asyncio
from datetime import date, timedelta

from finpipe import Client, FinpipeConfig

async def main() -> None:
    config = FinpipeConfig.load()  # discovers finpipe.settings.json
    async with Client(config) as client:
        end = date.today()
        start = end - timedelta(days=30)

        # Macro (FRED) — routed capability I/O
        cpi = await client.catalog.capability("macro").get_macro_series("CPIAUCSL", start, end)

        # Equity (Yahoo) — explicit provider I/O
        equity = client.catalog.capability("equity")
        prices = await equity.provider("yahoo").get_historical_prices("AAPL", start, end)
        meta = await equity.provider("yahoo").get_metadata("AAPL")

        print(len(cpi), meta.short_name)

asyncio.run(main())
```

---

## Installation & dependencies

### Core package (always installed)

| Dependency | Purpose |
|------------|---------|
| `polars`, `pandas`, `pyarrow` | Time-series DataFrames |
| `pydantic` | Config and structured return models |
| `tenacity`, `pybreaker` | Retries and circuit breaking |
| `cachetools` | In-memory cache backend |
| `curl_cffi` | HTTP transport for scraping providers (when wired) |

### Required for typical HTTP providers

| Extra / package | Install | Used by |
|-----------------|---------|---------|
| **`httpx`** | `pip install finpipe[httpx]` or `pip install httpx[http2]` | FRED, Alpha Vantage, Massive REST, Groq, Gemini, StockTwits (via `ResilientHttpClient`) |

> **Note:** `httpx` is not listed in core dependencies but is required at runtime for most HTTP adapters today.

### Optional extras (install what you use)

| Extra | Packages | Providers |
|-------|----------|-----------|
| `yahoo` | `yfinance` | Yahoo equity, metadata, options |
| `fred` | `fredapi` | Optional; FRED REST via httpx is the default implementation |
| `massive` | `aioboto3` | Massive REST + S3 flatfiles |
| `sentiment` | `feedparser` | Optional RSS parsing enhancement |
| `all` | All of the above | Full provider stack |

Recommended install for applications using multiple providers:

```bash
pip install finpipe[httpx,yahoo,fred,massive,sentiment]
```

### Application responsibilities

| Responsibility | Owner |
|----------------|-------|
| Python 3.12+ runtime | Application |
| Installing finpipe + extras | Application |
| `finpipe.settings.json` (or `FINPIPE_CONFIG`) | Application |
| API keys and S3 credentials (env vars) | Application |
| Creating / sharing `Client` instances | Application |
| Calling `asyncio.run()` or running inside an async event loop | Application |
| Writable cache directory (if `cache.cache_type: sqlite`) | Application |

finpipe creates `.cache/finpipe/` for rate-limit SQLite and default cache paths when configured — ensure the process can write there.

---

## Configuration (`finpipe.settings.json`)

### Discovery order

`FinpipeConfig.load()` resolves settings in this order:

1. **`FINPIPE_CONFIG`** env var (explicit file path), if set
2. Explicit `path=` argument to `load()`
3. First existing file among:
   - `./finpipe.settings.json`
   - `./.finpipe/settings.json`
   - `$XDG_CONFIG_HOME/finpipe/settings.json` (or `~/.config/finpipe/settings.json`)
   - `%APPDATA%/finpipe/settings.json` (Windows)
4. If none found → **defaults only** (`FinpipeConfig.from_env()`)

### Loading with overrides

```python
from finpipe import FinpipeConfig

# Auto-discover
config = FinpipeConfig.load()

# Explicit path
config = FinpipeConfig.from_file("finpipe.settings.json")

# Base + local overrides (deep-merge; local wins)
config = FinpipeConfig.from_file(
    "finpipe.settings.json",
    local_path="finpipe.settings.local.json",
)

# Programmatic override
config = FinpipeConfig.from_dict({"dataframe_format": "pandas"})
```

Partial JSON is valid: omitted keys keep library defaults (deep-merge over defaults).

### Top-level settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `dataframe_format` | `"polars"` \| `"pandas"` | `"polars"` | Format for time-series DataFrames |
| `cache` | object | see below | Cache backend and TTL storage |
| `routing` | object | see below | Primary/fallback provider routing for composite facades |
| `providers` | object | per-provider defaults | Rate limits, TTLs, HTTP transport per provider |

### `cache`

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `cache_type` | `"memory"` \| `"sqlite"` \| `"none"` | `"memory"` | Cache backend |
| `sqlite_path` | string | — | SQLite DB path (preferred key) |
| `sqlite_db_path` | string | `"finpipe_cache.db"` | Legacy/alternate SQLite path |
| `maxsize` | int | `1024` | Max entries (memory cache) |
| `namespace` | string | `"default"` | Cache namespace within the app |
| `singleton` | bool | `true` | Share one cache backend per process |

When `cache_type` is `"sqlite"`, learned AIMD rates are stored in the same database. Otherwise rates go to `.cache/finpipe/rate_limits.db`.

### `routing`

Controls which named provider adapters routed capabilities call first, and which to try on failure. Intel routes through the sentiment adapter (`providers.sentiment` sources).

| Key | Default | Description |
|-----|---------|-------------|
| `equity_primary` | `"yahoo"` | Primary equity provider |
| `equity_fallback` | `"alpha_vantage"` | Fallback equity provider |
| `options_primary` | `"massive"` | Primary options provider |
| `options_fallback` | `"yahoo"` | Fallback options provider |
| `llm_primary` | `"groq"` | Primary LLM provider |
| `llm_fallback` | `"gemini"` | Fallback LLM provider |

### `providers.<name>`

Each provider block supports:

| Key | Description |
|-----|-------------|
| `enabled` | Toggle (default `true`); when `false`, `ensure_configured()` is a no-op and missing API keys are not validated at `Client()` init |
| `model` | LLM only (`groq`, `gemini`) — default chat model when `generate_response` is called without `model=` |
| `temperature` | LLM only — default sampling temperature |
| `max_tokens` | LLM only — default completion token cap |
| `use_dynamic_model` | Groq only — when `true`, resolve newest Llama 70B via models API instead of `model` |
| `rate_limits` | Hard caps and HTTP resilience (see below) |
| `ttls` | Cache freshness per data type (seconds; `0` = no cache write) |
| `http` | Transport/timeouts (`transport`, `impersonate`, `user_agent`, …) |
| `sources` | Sentiment only — per-source config (`google_news`, `stocktwits`, `reddit`) |

#### `rate_limits` (user-tunable)

| Key | Default | Description |
|-----|---------|-------------|
| `max_requests_per_second` | `5.0` | Hard RPS cap (AIMD never exceeds this) |
| `max_requests_per_minute` | `null` | Optional RPM cap (LLM providers) |
| `max_tokens_per_minute` | `null` | Optional TPM cap (LLM providers) |
| `max_retries` | `3` | HTTP retry attempts |
| `circuit_breaker_failure_threshold` | `5` | Failures before circuit opens |
| `circuit_breaker_recovery_timeout_sec` | `60.0` | Circuit half-open delay |
| `backoff_multiplier` | `1.5` | Exponential backoff base |

AIMD adaptive tuning (increase/decrease rates, burst size) is **internal** — not configurable in JSON. Unknown AIMD keys in JSON are rejected (`extra="forbid"`).

#### Example minimal settings (Yahoo + FRED only)

```json
{
  "dataframe_format": "pandas",
  "cache": {
    "cache_type": "memory",
    "maxsize": 2048
  },
  "providers": {
    "yahoo": {
      "enabled": true,
      "rate_limits": { "max_requests_per_second": 2.0 }
    },
    "fred": {
      "enabled": true,
      "rate_limits": { "max_requests_per_second": 2.0 }
    }
  }
}
```

Full example: [finpipe.settings.example.json](./finpipe.settings.example.json).

---

## Environment variables

### Secrets (required by provider)

Secrets are read from the **process environment** at config/adapter initialization. Never commit them to `finpipe.settings.json`.

| Variable | Provider | Required when |
|----------|----------|---------------|
| `FRED_API_KEY` | FRED | Provider is `enabled: true` in settings (validated at `Client()` init) |
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage | Provider is `enabled: true` |
| `MASSIVE_API_KEY` | Massive REST | Provider is `enabled: true` |
| `MASSIVE_ACCESS_KEY_ID` | Massive S3 | Provider is `enabled: true` |
| `MASSIVE_SECRET_ACCESS_KEY` | Massive S3 | Provider is `enabled: true` |
| `MASSIVE_S3_ENDPOINT` | Massive S3 | Provider is `enabled: true` (e.g. `https://files.massive.com`) |
| `MASSIVE_S3_BUCKET` | Massive S3 | Provider is `enabled: true` (e.g. `flatfiles`) |
| `GROQ_API_KEY` | Groq | Provider is `enabled: true` |
| `GEMINI_API_KEY` | Gemini | Provider is `enabled: true` |
| `NVIDIA_API_KEY` | NVIDIA NIM | Provider is `enabled: true` |

**No API key required:** Yahoo, TradingView, sentiment sources (Google News, StockTwits, Reddit).

> **Note (v0.5.0):** `Client()` eagerly constructs all provider adapters via an internal registry. All public I/O goes through `client.catalog` capability and provider handles.

### finpipe configuration overrides

| Variable | Description |
|----------|-------------|
| `FINPIPE_CONFIG` | Absolute or relative path to settings JSON (skips auto-discovery) |
| `FINPIPE_CACHE_BACKEND` | Overrides `cache.cache_type` (`memory`, `sqlite`, `none`) |

### Programmatic key access

```python
config = FinpipeConfig.load()
api_key = config.get_required_key("fred_api_key")  # reads FRED_API_KEY from env
```

Supported keys: `fred_api_key`, `alpha_vantage_api_key`, `groq_api_key`, `gemini_api_key`, `massive_api_key`.

---

## Using `Client`

### Construction

```python
from finpipe import Client, FinpipeConfig

client = Client()                          # FinpipeConfig.load()
client = Client(FinpipeConfig.load())        # explicit config
client = Client(FinpipeConfig(dataframe_format="pandas"))
```

### Lifecycle

```python
async with Client(config) as client:
    ...
# closes HTTP sessions for adapters that support close()

# or manually:
client = Client(config)
try:
    ...
finally:
    await client.close()
```

`close()` shuts down HTTP clients for: Alpha Vantage, Massive, FRED, TradingView, sentiment, Groq, Gemini. Yahoo uses yfinance (no HTTP session to close).

### Public API (v0.5.0)

| Surface | Access | Description |
|---------|--------|-------------|
| **`client.catalog`** | Capability and provider handles | **Only public I/O path** — routed composites and explicit provider adapters |
| **`client.health`** | Health probes | Connectivity checks for catalog providers |
| **`client.dump_settings()`** | Config introspection | Resolved settings snapshot |

Legacy direct attributes (`client.yahoo`, `client.equity`, …) were removed in v0.5.0.

**Routed I/O** — call async methods on a capability handle; composites apply primary/fallback routing:

```python
equity = client.catalog.capability("equity")
meta = await equity.get_metadata("AAPL")
chain = await equity.get_options_chain("AAPL")
```

**Explicit I/O** — call a specific provider within a capability:

```python
yahoo = client.catalog.capability("equity").provider("yahoo")
meta = await yahoo.get_metadata("AAPL")
```

Routing is controlled in `finpipe.settings.json`:

```json
{
  "routing": {
    "equity_primary": "yahoo",
    "equity_fallback": "alpha_vantage",
    "options_primary": "massive",
    "options_fallback": "yahoo"
  }
}
```

On failure, composites try the fallback provider before raising `FinpipeProviderDownError`.

### Composite examples

```python
from finpipe.core.models import SocialPostKind

async with Client(config) as client:
    equity = client.catalog.capability("equity")
    options = client.catalog.capability("options")
    intel = client.catalog.capability("intel")

    meta = await equity.get_metadata("AAPL")
    chain = await equity.get_options_chain("AAPL")
    snaps = await options.fetch_options_snapshot("SPY", limit=50)
    headlines = await intel.get_news("NVDA", limit=10)
    forum_posts = await intel.get_social_posts("TSLA", kind=SocialPostKind.FORUM)
    sentiment = await intel.get_sentiment_score("TSLA")
```

LLM calls use explicit provider refs (no routed composite):

```python
groq = client.catalog.capability("llm").provider("groq")
response = await groq.generate_response("Summarize AAPL earnings")
```

Named provider refs remain addressable per catalog row (e.g. `yahoo` appears twice — equity and options).

### Concurrent fetches

finpipe throttles per provider namespace (AIMD rate limit + in-flight cap). Your app may run many tasks concurrently:

```python
import asyncio

async with Client(config) as client:
    equity = client.catalog.capability("equity")
    macro = client.catalog.capability("macro")
    results = await asyncio.gather(
        equity.provider("yahoo").get_metadata("AAPL"),
        equity.provider("yahoo").get_metadata("MSFT"),
        macro.get_macro_series("GDP", start, end),
    )
```

---

## Package exports

```python
from finpipe import (
    Client,
    FinpipeConfig,
    FinpipeError,
    FinpipeConfigError,
    FinpipeDataNotFoundError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
    __version__,
)
```

Additional types (import explicitly; not in `finpipe.__all__`):

```python
from finpipe.core.interfaces import IHistoricalPriceProvider, IOptionsProvider, ...
from finpipe.core.models import TickerMetadata, OptionChain, SentimentScore, SocialPost, SocialPostKind, LLMResponse, ...
```

---

## Capability protocols (target API)

Defined in `finpipe.core.interfaces`. All I/O methods are `async def`.

### `IHistoricalPriceProvider`

| Method | Returns | Notes |
|--------|---------|-------|
| `get_historical_prices(symbol, start_date, end_date, interval="1d")` | `DataFrame` | OHLCV schema — see below |
| `get_live_spot_price(symbol)` | `float \| None` | On Yahoo/Alpha Vantage adapters; **not yet on protocol** |

### `IMetadataProvider`

| Method | Returns |
|--------|---------|
| `get_metadata(symbol)` | `TickerMetadata` |
| `get_financial_statements(symbol)` | `dict[str, Any]` |

### `IOptionsProvider`

| Method | Returns |
|--------|---------|
| `get_options_chain(symbol, expiration_date=None)` | `OptionChain` |
| `get_options_snapshot(symbol, **filters)` | `DataFrame` |

### `IMacroProvider`

| Method | Returns |
|--------|---------|
| `get_macro_series(series_id, start_date, end_date)` | `DataFrame` (`timestamp`, `value`) |

### `IMarketIntelProvider`

| Method | Returns |
|--------|---------|
| `get_news(symbol=None, limit=20)` | `list[NewsArticle]` — all enabled news sources |
| `get_social_posts(symbol, *, limit=30, kind=None)` | `list[SocialPost]` — forum/microblog; `kind` filters channel |
| `get_sentiment_score(symbol)` | `SentimentScore` |

### `ILLMProvider`

| Method | Returns |
|--------|---------|
| `generate_response(prompt, model=None, **kwargs)` | `LLMResponse` |

### `IProviderDescribe`

| Method | Returns |
|--------|---------|
| `describe()` | `dict[str, Any]` — JSON-serializable provider metadata (settings, limits, provider-specific `details`) |

Every client adapter implements `describe()`. LLM providers include remote `models` in `details`; intel/screener adapters include per-source settings. Secrets in `settings` are redacted to `"<configured>"`.

```python
groq_info = await client.catalog.capability("llm").provider("groq").describe()
# {
#   "provider_id": "groq",
#   "capability": "llm",
#   "enabled": true,
#   "configured": true,
#   "settings": { "rate_limits": {...}, "model": "...", ... },
#   "details": { "default_model": "...", "models": [...], "temperature": 0.3, ... }
# }
```

### Screener capability — `client.catalog.capability("screener")`

Routed methods on the screener capability handle:

| Method | Returns |
|--------|---------|
| `run(source, **params)` | `list[str]` — dispatch by source name |
| `get_trending()` | `list[str]` — Yahoo US trending equities |
| `get_predefined(scr_id, *, limit=None)` | `list[str]` — Yahoo predefined lists |
| `get_fundamental(filter_key)` | `list[str]` — Finviz screener filter |
| `run_tradingview(criteria)` | `list[str]` — TradingView scanner POST |

Source names for `run()`: `yahoo_trending`, `yahoo_predefined`, `finviz`, `tradingview`.

Configure per-source limits under `providers.screener.sources` in `finpipe.settings.json` (mirrors `providers.sentiment.sources`). Legacy `providers.tradingview` merges into `screener.sources.tradingview`.

### `client.health` — provider connectivity probes

Optional lightweight checks that providers respond (similar to aksh `provider_contract_probes`).

| Method | Returns |
|--------|---------|
| `list_probe_keys()` | `list[str]` — probe keys that will run |
| `check(probe_key)` | `ProbeResult` — one probe |
| `check_all()` | `HealthReport` — all configured probes |

Configure in `finpipe.settings.json`:

```json
"health": {
  "enabled": true,
  "probe_symbol": "SPY",
  "probes": {
    "equity.yahoo": { "enabled": true },
    "screener.yahoo_trending": { "enabled": true },
    "llm.groq": { "enabled": true }
  }
}
```

When `probes` is empty, all probes for **enabled** providers run. Probe keys use `{capability}.{provider_or_source}` (e.g. `intel.google_news`, `screener.finviz`, `options.massive`).

`ProbeResult.status` is one of: `connected`, `degraded`, `unconfigured`, `error`, `disabled`, `skipped`.

| Method | Returns |
|--------|---------|
| `describe_probes()` | `list[HealthProbeCatalogEntry]` — probe catalog merged with config (no HTTP) |
| `health_config_template()` | `dict` — suggested `health.probes` JSON block |

### `client.catalog` — capability and provider handles

All application I/O goes through catalog handles. Inventory methods require no HTTP.

| Method | Returns |
|--------|---------|
| `capabilities()` | `list[CapabilityHandle]` — sorted alphabetically by id |
| `capability(name)` | `CapabilityHandle` — one capability group |
| `list_health_probes()` | Same probes as `health.describe_probes()` |
| `health_config_template()` | Suggested `health.probes` toggles from current config |

Each `CapabilityHandle` exposes:

| Member | Returns |
|--------|---------|
| `id` | Capability name (`equity`, `options`, …) |
| `describe()` | `dict` — static metadata + routing (sync, no HTTP) |
| `providers()` | `list[ProviderRef]` — all catalog rows for this capability |
| `provider(provider_id)` | `ProviderRef` — one provider/source row |
| `routing.primary` / `.fallback` | `ProviderRef \| None` — from routing config |
| async methods (e.g. `get_metadata`) | Routed composite I/O (not on `llm`) |

Each `ProviderRef` exposes catalog fields (`provider_id`, `capability`, `label`, `enabled`, …), async `describe()`, and delegates adapter methods (e.g. `get_metadata`, `generate_response`).

Example:

```python
for cap in client.catalog.capabilities():
    print(cap.id, cap.describe()["label"])

equity = client.catalog.capability("equity")
for ref in equity.providers():
    print(ref.provider_id, ref.enabled, ref.health_probe_key)

yahoo = equity.provider("yahoo")
meta = await yahoo.get_metadata("AAPL")
live = await yahoo.describe()

llm_primary = client.catalog.capability("llm").routing.primary
response = await llm_primary.generate_response("Hello")

template = client.catalog.health_config_template()
# paste into finpipe.settings.json under "health": { "probes": template }
```

### `ILLMProvider`

| Method | Returns |
|--------|---------|
| `generate_response(prompt, model=None, **kwargs)` | `LLMResponse` |

### `ICloseable`

| Method | Description |
|--------|-------------|
| `close()` | Release HTTP resources |

---

## Provider adapters (via catalog handles)

Adapters are internal; access them through `client.catalog.capability(...).provider(...)`. Methods match the protocols above unless noted.

### Yahoo Finance — `capability("equity"|"options").provider("yahoo")`

| Method | Extra deps | API key |
|--------|------------|---------|
| `get_historical_prices` | `finpipe[yahoo]` | — |
| `get_live_spot_price` | `finpipe[yahoo]` | — |
| `get_metadata` | `finpipe[yahoo]` | — |
| `get_financial_statements` | `finpipe[yahoo]` | — |
| `get_options_chain` | `finpipe[yahoo]` | — |
| `get_options_snapshot` | `finpipe[yahoo]` | — |

Uses `yfinance` behind `asyncio.to_thread`.

### Alpha Vantage — `capability("equity").provider("alpha_vantage")`

| Method | API key |
|--------|---------|
| `get_historical_prices` | `ALPHA_VANTAGE_API_KEY` |
| `get_live_spot_price` | `ALPHA_VANTAGE_API_KEY` |
| `get_metadata` | `ALPHA_VANTAGE_API_KEY` |
| `get_financial_statements` | `ALPHA_VANTAGE_API_KEY` |

### FRED — `capability("macro").provider("fred")` or routed `capability("macro").get_macro_series(...)`

| Method | API key |
|--------|---------|
| `get_macro_series(series_id, start_date, end_date)` | `FRED_API_KEY` |

### Massive — `capability("options").provider("massive")`

| Method | API key |
|--------|---------|
| `get_options_chain` | `MASSIVE_API_KEY` (+ S3 env vars for flatfiles) |
| `get_options_snapshot` | `MASSIVE_API_KEY` |

### TradingView — `capability("screener").provider("tradingview")`

| Method | API key |
|--------|---------|
| `run_screener(criteria)` | — |

### Sentiment (intel sources) — `capability("intel").*` routed composite

Intel provider rows (`google_news`, `stocktwits`, `reddit`) map to the shared sentiment adapter. Use routed capability methods (`get_news`, `get_social_posts`, `get_sentiment_score`) or health probes per source.

Sources configured under `providers.sentiment.sources` (`google_news`, `stocktwits`, `reddit`).

### Groq / Gemini / NVIDIA — `capability("llm").provider("groq"|"gemini"|"nvidia")`

| Method | API key |
|--------|---------|
| `generate_response(prompt, model=None, **kwargs)` | `GROQ_API_KEY` / `GEMINI_API_KEY` / `NVIDIA_API_KEY` |
| `describe()` | Same — includes remote model list in `details.models`; used by `client.health` LLM probes |

Default model when `model` is omitted: `providers.groq.model` (`llama3-8b-8192`), `providers.gemini.model` (`gemini-1.5-flash`), or `providers.nvidia.model` (`meta/llama-3.1-70b-instruct`). Per-call `model=` overrides the settings default.

---

## Return types & schemas

### Time-series DataFrames

Controlled by `FinpipeConfig.dataframe_format` (`"polars"` or `"pandas"`).

**OHLCV (historical prices):**

| Column | Description |
|--------|-------------|
| `timestamp` | Bar timestamp |
| `open`, `high`, `low`, `close` | Prices |
| `volume` | Volume |

**Macro (FRED):**

| Column | Description |
|--------|-------------|
| `timestamp` | Observation date |
| `value` | Series value |

### Pydantic models (`finpipe.core.models`)

| Model | Fields (required in bold) |
|-------|---------------------------|
| `TickerMetadata` | **symbol**, short_name, long_name, sector, industry, market_cap, exchange, currency, website, description |
| `OptionContract` | **contract_symbol**, **strike**, **in_the_money**, last_price, bid, ask, volume, open_interest, implied_volatility |
| `OptionChain` | **symbol**, **expiration_date**, calls, puts |
| `NewsArticle` | **title**, **link**, **published_at**, publisher, summary, related_tickers |
| `SentimentScore` | **source**, **timestamp**, **score**, symbol, magnitude |
| `LLMResponse` | **model_name**, **content**, prompt_tokens, completion_tokens, raw_response |

All models use `extra="allow"` for forward-compatible fields.

---

## Exceptions

All inherit from `FinpipeError`.

| Exception | When raised |
|-----------|-------------|
| `FinpipeConfigError` | Missing env vars, invalid settings, cache self-test failure |
| `FinpipeDataNotFoundError` | Series/symbol/resource not found |
| `FinpipeRateLimitExceededError` | Rate limit exhausted after retries |
| `FinpipeProviderDownError` | Network failure, 5xx, circuit breaker open |
| `FinpipeParseError` | Unparseable provider payload |

Example handling:

```python
from finpipe import (
    Client,
    FinpipeDataNotFoundError,
    FinpipeProviderDownError,
    FinpipeRateLimitExceededError,
)

async with Client() as client:
    try:
        equity = client.catalog.capability("equity")
        df = await equity.provider("yahoo").get_historical_prices("AAPL", start, end)
    except FinpipeRateLimitExceededError:
        ...  # backoff or use stale cache (future)
    except FinpipeProviderDownError:
        ...  # provider unavailable
    except FinpipeDataNotFoundError:
        ...  # bad symbol or empty series
```

---

## Inspecting resolved settings

```python
async with Client(config) as client:
    settings = client.dump_settings()           # dict, secrets redacted by default
    json_str = client.dump_settings_json()    # pretty JSON

# or on config directly:
config.dump_settings(redact_secrets=False)    # includes env-sourced keys — avoid logging
```

Use this to verify effective rate limits, TTLs, routing, and transport per provider after merge with defaults.

---

## Development & quality gates

Contributors and local development use the same checks as pre-commit.

### One-time setup

```powershell
# Windows
pip install -e ".[dev,httpx,yahoo,fred,massive,sentiment]"
pre-commit install
```

```bash
# Linux / macOS
pip install -e ".[dev,httpx,yahoo,fred,massive,sentiment]"
pre-commit install
```

The `dev` extra installs `ruff`, `basedpyright`, `pyrefly`, `pytest`, and `aioboto3` (for type-checking the Massive adapter).

### Run all checks

```powershell
.\scripts\run_checks.ps1
```

```bash
./scripts/run_checks.sh
```

This runs, in order: **typecheck import root** → **ruff** → **basedpyright** → **pyrefly** → **pytest** (≥95% coverage).

### Type-check import root

Source lives under `src/` but installs as the `finpipe` package. Static analyzers need a `finpipe/` directory on the import path without duplicating `src/` in the same check.

`scripts/ensure_typecheck_import_root.py` creates a junction at `typecheck/finpipe` → `src` (gitignored). `basedpyright`, `pyrefly`, and pytest (`pythonpath = ["typecheck"]` in `pyproject.toml`) use that alias. Pre-commit and `run_checks` call the ensure script automatically.

See [architecture.md](./architecture.md#development-workflow-and-quality-gates) for hook details and the architecture-doc sync rule.

---

## Related files

| File | Purpose |
|------|---------|
| [finpipe.settings.example.json](./finpipe.settings.example.json) | Full settings template |
| [architecture.md](./architecture.md) | System design, rate limiting, stability rules |
| [../scripts/run_checks.ps1](../scripts/run_checks.ps1) | Local quality gate (ruff, type checkers, pytest) |
| [../scripts/test_pipeline.py](../scripts/test_pipeline.py) | End-to-end integration script |

---

## Version

This document matches finpipe **v0.5.0** with the catalog-centric public API (`client.catalog` capability and provider handles). Direct `client.yahoo`, `client.equity`, and similar attributes were removed; use routed capability I/O or explicit provider refs.
