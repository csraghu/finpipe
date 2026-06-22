# `finpipe` Architectural Design

**Purpose:** `finpipe` is a standalone, extensible Python package designed to provide a unified, robust, and performant interface for fetching financial data across various providers (e.g., Yahoo Finance, FRED, FMP). It isolates the complexity of rate limiting, data parsing, and provider-specific quirks from the consuming application (like `aksh`).

---

## 1. Core Abstractions & Extensible APIs

To allow `aksh` (and future applications) to swap providers without rewriting business logic, `finpipe` will use a strict interface-driven design. It will expose a clean, highly scalable top-level API while keeping the internal machinery completely extensible.

### Top-Level Facade (The Public API)
Consumers will interact with a unified entry point (e.g., `finpipe.Client(config=my_config)`). This client will seamlessly route requests to the appropriate underlying provider based on user parameters, ensuring the public API remains simple even as massive new datasets or services are added.

### The `DataProvider` Protocol (Extensibility)
Every provider in `finpipe` will adhere to a core interface. Instead of a massive God-class, we use interface segregation so applications can request exactly the capabilities they need. Based on the requirements extracted from `aksh` (historical pricing, options snapshots, metadata, financials, and market intel/sentiment), here are the extensible interfaces:

```python
from typing import Protocol, Any
from datetime import date
import polars as pl
from .models import TickerMetadata, OptionChain, NewsArticle, SentimentScore, LLMResponse

class IHistoricalPriceProvider(Protocol):
    async def get_historical_prices(self, symbol: str, start_date: date, end_date: date, interval: str = "1d") -> pl.DataFrame:
        """Fetch OHLCV bars. Returns Polars DataFrame: timestamp, open, high, low, close, volume."""
        ...
        
    async def get_live_spot_price(self, symbol: str) -> float | None:
        """Fetch the current real-time or delayed spot price."""
        ...

class IMetadataProvider(Protocol):
    async def get_metadata(self, symbol: str) -> TickerMetadata:
        """Fetch general company metadata, market cap, sectors, etc."""
        ...
        
    async def get_financial_statements(self, symbol: str) -> dict[str, Any]:
        """Fetch balance sheets, income statements, and cash flows."""
        ...

class IOptionsProvider(Protocol):
    async def get_options_chain(self, symbol: str, expiration_date: date | None = None) -> OptionChain:
        """Fetch full options chain for a given expiration."""
        ...
        
    async def get_options_snapshot(self, symbol: str, **filters) -> pl.DataFrame:
        """Fetch a cross-section of options data (useful for massive flatfile integration)."""
        ...

class IMacroProvider(Protocol):
    async def get_macro_series(self, series_id: str, start_date: date, end_date: date) -> pl.DataFrame:
        """Fetch macroeconomic rates (like the Risk-Free Rate, GDP). Returns Polars DataFrame."""
        ...

class IMarketIntelProvider(Protocol):
    async def get_news(self, symbol: str | None = None, limit: int = 20) -> list[NewsArticle]:
        """Fetch recent news articles (Ticker-specific or Macro if symbol is None)."""
        ...
        
    async def get_sentiment_score(self, symbol: str) -> SentimentScore:
        """Fetch or calculate standard sentiment scores from lexicon/LLM analysis."""
        ...

class IScreenerProvider(Protocol):
    async def run_screener(self, criteria: dict[str, Any]) -> list[str]:
        """Execute a market screen and return a list of matching symbols."""
        ...

class ILLMProvider(Protocol):
    async def generate_response(self, prompt: str, model: str | None = None, **kwargs: Any) -> LLMResponse:
        """Generate AI completions (Groq/Gemini)."""
        ...
```

### Provider to Interface Mapping
To provide clarity on how the adapters will be built, here is the explicit mapping of the active providers to the interfaces they will implement:

*   **YahooFinanceAdapter:** `IHistoricalPriceProvider`, `IMetadataProvider`, `IOptionsProvider`
*   **AlphaVantageAdapter:** `IHistoricalPriceProvider`, `IMetadataProvider`
*   **MassiveOptionsAdapter:** `IOptionsProvider`
*   **FredAdapter:** `IMacroProvider`
*   **TradingViewAdapter:** `IScreenerProvider`
*   **NewsSentimentAdapter (Google/WSJ/Reddit/Stocktwits):** `IMarketIntelProvider`
*   **GroqAdapter / GeminiAdapter:** `ILLMProvider`

**Adding New Services:** Because of this protocol-driven design, adding a completely new data source (e.g., a Crypto provider or a News Sentiment provider) simply requires creating a new adapter class that implements the relevant interface and registering it with the client. The core scaling architecture (rate limits, caching, circuit breakers) will automatically wrap and protect the new provider.

---

## 2. Standardized Data Formats

A major goal of `finpipe` is to ensure that no matter which provider is used underneath, the data comes out looking exactly the same.

### Time-Series Data (`polars` / `pandas`)
All time-series data (OHLCV, macroeconomic rates) will be returned natively as **Polars DataFrames** by default. 
*   *Why Polars?* Polars is significantly faster and more memory-efficient than Pandas. By standardizing on Polars at the `finpipe` boundary, we remove the need for downstream applications to run adapter scripts (like `pandas_ohlcv_to_polars`).
*   **Optional Pandas Support:** While Polars is the default, `finpipe` supports native Pandas DataFrames via the configuration flag (`dataframe_format="pandas"`). The core pipeline handles the efficient conversion just before returning the data to the downstream application.
*   Schema enforcement: `finpipe` will guarantee the output schema (e.g., columns `["timestamp", "open", "high", "low", "close", "volume"]` will always exist and be of specific dtypes) regardless of the DataFrame format selected.

### Structured Data (Pydantic)
All metadata, options chains, and financial statements will be returned as strictly typed **Pydantic Models** rather than arbitrary dictionaries. 
*   *Example:* A `TickerProfile` model will ensure `market_cap` is always a float, preventing downstream `KeyError`s.

### Backward Compatibility Guarantees
To ensure `finpipe` can evolve without breaking `aksh` or other consumers:
1. **Pydantic Models:** All models are configured with `ConfigDict(extra="allow")`. If a provider suddenly returns new fields (or if we expand the finpipe models in future versions), the models will gracefully accept the new data without failing validation.
2. **Polars DataFrames:** Downstream applications must select columns by name, not index. New columns added to DataFrames in future `finpipe` versions will be purely additive and will not break existing row/column layouts.
3. **API Versioning:** If a fundamentally breaking change is required (e.g., radically changing how option chains are structured), `finpipe` will expose versioned interfaces (e.g., `IOptionsProviderV2`) rather than mutating the V1 contract.

---

## 3. Network, Resilience & Rate Limiting (The "Pipe")

As per the `finpipe-rules.xml` (Circuit Breakers & Retries) and `finpipe-eng-handbook.xml`, data providers notoriously fail, rate-limit, or return malformed data. `finpipe` must handle this gracefully internally, completely shielding the downstream application from over-fetching failures.

### Inbuilt Provider-Specific Rate Limiting
Instead of letting the downstream app blindly overwhelm an API, `finpipe` will enforce strict, provider-specific rate limits internally.
*   **Token Bucket Algorithm with Steady-State Throttling:** `finpipe.network.limiter` will implement an async-safe `TokenBucketRateLimiter`. Instead of bursting and hitting 429s, it will proactively `asyncio.sleep()` just enough to maintain a smooth, continuous flow of requests at exactly the maximum safe rate. This ensures throughput remains perfectly stable near 100% utilization without spilling over.
*   **Configurable Provider Quotas:** Each provider will define its own default safe rate limit (e.g., Yahoo = 5 req/sec). However, consumers can completely override these default limits at runtime by passing custom rate limits into the `FinpipeConfig` (useful if you have a paid API key with higher limits).
*   **Dynamic TCP-style Congestion Control:** To handle graceful recovery, if a provider unexpectedly throws a `429 Too Many Requests` (e.g., they dynamically lowered their own limits under load), the rate limiter will dynamically scale back its token generation rate (halving the throughput). As the provider recovers and requests succeed, the limiter will slowly ramp the throughput back up to the target 100% capacity limit.
*   **Concurrency Safe:** The rate limiter will use `asyncio.Lock` or `threading.Lock` to ensure that even if the downstream app spawns 1,000 concurrent tasks, `finpipe` will seamlessly queue and throttle them down to the provider's steady-state limit.

### Retries, Jitter, and Circuit Breakers
In alignment with the engineering handbook, every external HTTP call will be wrapped with a robust resilience layer:
*   **Retries with Jitter:** Using `tenacity`, transient failed requests (like `502 Bad Gateway`) will be retried automatically with exponential backoff and randomized jitter to prevent thundering herds.
*   **Circuit Breakers:** If a provider is completely down (e.g., 5 consecutive 500 errors), `finpipe` will trip a circuit breaker. This immediately "fails fast" for subsequent requests until a cooldown period passes, saving network resources and preventing cascading failures.
*   **Configurable Parameters:** All retry counts, backoff multipliers, limits, and circuit breaker thresholds will be purely configurable via an immutable `FinpipeConfig` dataclass injected at runtime.

### Standardized Error Hierarchy
Downstream apps should not need to catch `yfinance.YFException` or `httpx.ReadTimeout`. `finpipe` will catch provider-specific errors and raise a standardized hierarchy:
*   `FinpipeRateLimitExceededError` (if the circuit breaker or hard quotas are fundamentally breached)
*   `FinpipeDataNotFoundError`
*   `FinpipeProviderDownError`

---

## 4. Caching Layer

`finpipe` will define an abstract `ICacheBackend` to eliminate redundant network calls and dramatically improve response times.
*   **Provider & Endpoint Specific TTLs:** The internal cache will support granular Time-To-Live (TTL) configurations. For instance, historical daily prices might be safely cached for 12 hours, whereas intraday minute-data might be cached for only 1 minute, and static metadata for 7 days.
*   **Configurable Overrides:** `finpipe` will ship with sensible default TTLs for every endpoint. However, consumers can completely overwrite these defaults at runtime by passing custom TTL values into the `FinpipeConfig`.
*   **Cache-First Architecture:** Providers will always check the cache *before* checking the rate limiter or attempting any network calls.
*   **Default Implementation:** The package will ship with an `InMemoryTTLCache` by default (powered by `cachetools`).
*   **Extensibility:** Consumers can inject their own persistent cache backend (like Redis or SQLite) if they require caching across application restarts or multiple worker processes.

---

## 5. Package Structure

```text
finpipe/
├── docs/
│   ├── finpipe_architecture.md
│   ├── finpipe-rules.xml
│   ├── finpipe-eng-handbook.xml
├── src/
│   ├── finpipe/
│   │   ├── core/
│   │   │   ├── interfaces.py      # Abstract base classes
│   │   │   ├── models.py          # Pydantic data schemas
│   │   │   ├── exceptions.py      # Standardized errors
│   │   │   ├── config.py          # FinpipeConfig dataclasses (Rate Limits & TTLs)
│   │   ├── network/
│   │   │   ├── client.py          # Default HTTP clients
│   │   │   ├── limiter.py         # Dynamic TokenBucketRateLimiter implementation
│   │   │   ├── resilience.py      # Tenacity retries and Circuit Breakers
│   │   │   ├── cache.py           # Caching interfaces and InMemoryTTLCache
│   │   ├── providers/
│   │   │   ├── yahoo/             # YFinance implementation with its specific limits
│   │   │   ├── fred/              # Macro data implementation
│   │   │   ├── base.py            # Shared provider utilities

---

## 6. Implementation Roadmap & Phasing

To ensure a robust build, the package is being constructed and verified in the following discrete phases:

*   **Phase 1: Core Foundation & Network Engine (COMPLETED)**
    *   Build the `TokenBucketRateLimiter`, Circuit Breakers, `ICacheBackend` (InMemory & SQLite).
    *   Establish all standard `Protocol` interfaces (`IMetadataProvider`, `IHistoricalPriceProvider`, etc.).
    *   Define strict Pydantic schemas and configuration loading mechanisms.
*   **Phase 2: Equity & Options Providers (COMPLETED)**
    *   Implement `YahooFinanceAdapter` for OHLCV, Option Chains, and Metadata.
    *   Implement `AlphaVantageAdapter` for OHLCV and Metadata.
    *   Implement `MassiveOptionsAdapter` for high-fidelity option snapshots.
*   **Phase 3: Macro & Market Intel (PENDING)**
    *   Implement `FredAdapter` for macroeconomic rates.
    *   Implement `TradingViewAdapter` for market screeners.
    *   Implement `NewsSentimentAdapter` supporting Google News, WSJ, Reddit, and Stocktwits feeds.
*   **Phase 4: LLM Providers (PENDING)**
    *   Implement `GroqAdapter` and `GeminiAdapter` to route AI inference through the same robust rate-limiting and caching layers used for financial data.
```

---

## Open Questions

**1. Data Structure Preference:** Are you completely on board with `finpipe` exclusively returning `polars` DataFrames for all OHLCV/time-series data, or do you want it to optionally support `pandas` via a configuration flag?

**2. Scope of Initial Build:** For the first version of the package, should we focus *only* on porting the `yfinance` logic over to establish the architecture and the rate-limiter, or do you want to include others (like FRED) immediately?
