# finpipe — Architectural Review (2026-07-01)

Scope: `docs/` (architecture.md, finpipe_architecture.md, api-reference.md, rules/handbook XML), all of `src/`, the full `tests/` tree, `pyproject.toml`, and the graphify output (`graphify-out/2026-07-01/GRAPH_REPORT.md`, built from commit `2901a300`: 1406 nodes, 2820 edges, 104 communities). The Linux sandbox was unavailable this session, so findings are from static reading, not from executing the suite.

**Verdict:** The macro-architecture is genuinely good — interface-segregated protocols, composite routing, adaptive rate limiting, layered config. The implementation undermines it in four systemic ways: (1) the caching layer is silently broken for most payloads under SQLite, (2) eager construction/validation contradicts the documented lazy design, (3) the exception taxonomy and fallback rules the docs promise are not what the code does, and (4) secrets leak through the introspection surfaces that were built specifically to redact them. One provider (Schwab) cannot work at runtime at all, and the tests are written in a way that structurally cannot catch it.

---

## 1. What is good

**Layering and dependency direction.** `core` ← `network` ← `providers` ← `client` is clean and enforced; nothing in `core` imports upward. `_internal/` for AIMD tuning constants and hard caps is the right call — users tune hard caps, not the control loop.

**Interface segregation.** The capability protocols (`IHistoricalPriceProvider`, `IOptionsProvider`, `IMacroProvider`, …) are small, `runtime_checkable`, async-only, and match consumer needs rather than vendor shapes. This is the strongest part of the design.

**Rate limiting stack.** `AdaptiveRateLimiter` (AIMD + SQLite persistence of learned rate), `RpmTpmRateLimiter` (dual RPM/TPM buckets for LLMs with post-hoc token reconciliation via `reconcile_token_usage`), `DynamicConcurrencyLimiter` (in-flight cap derived from current rate), and hard-cap clamping in `_internal/limits.py` form a coherent, well-thought-out throttling design. Persisting the learned rate so restarts don't cold-start-spike is a nice touch.

**Config model.** Frozen Pydantic models, layered precedence (defaults → settings file → env → programmatic), deep merge, settings discovery paths, `extra="forbid"` on `RateLimitConfig` to reject AIMD tuning attempts, legacy-key migration (`_migrate_legacy_llm_prompt_settings`). Per-provider, per-endpoint TTLs are typed rather than stringly-keyed dicts — good.

**Normalization discipline.** Adapters translate vendor payloads into shared Pydantic models (`TickerMetadata`, `OptionChain`, `SentimentScore`) and canonical OHLCV columns; `SocialPostKind.FORUM/MICROBLOG` abstracts vendor names. Error translation into `Finpipe*Error` at the boundary is mostly present.

**Health and catalog introspection.** `HealthService` with per-probe results, latency, and settings-driven probe selection, plus `CatalogService`/`describe()` for provider inventory, is more operational maturity than most libraries this size have.

**Tooling posture.** ruff + two type checkers + pre-commit + coverage gates + `py.typed` + live-test gating via markers. The intent is right, even where execution slips (below).

---

## 2. Critical defects (broken behavior, fix first)

### 2.1 SchwabAdapter cannot work at runtime
`schwab.py` calls `self._client.post(...)`, `self._client.get(...)`, and `self._client.aclose()`. `ResilientHttpClient` exposes only `request()` and `close()`. Every Schwab call path — including `Client.close()`, which calls `adapter.close()` → `aclose()` — raises `AttributeError`. The tests never catch this because they replace `_client` with an `AsyncMock` (see §5). Schwab also bypasses the resilience layer's error mapping entirely (its own `raise_for_status`), so even after fixing the method names, its errors won't be translated.

### 2.2 SQLite cache silently no-ops for most payloads
`SqliteCacheBackend.set` does `json.dumps(value)` inside `except Exception: log warning`. Cached values routinely contain non-JSON types: `pd.Timestamp` (every `df.to_dict(orient="list")` from Yahoo/Fred/AV/Massive), `datetime`/`date` (every `model_dump()` of `NewsArticle`, `SentimentScore`, `OptionChain`). All of these fail serialization, log a warning, and store nothing. Net effect: with `cache_type="sqlite"` (the documented production mode) the cache works only for primitives (spot prices, token strings, LLM responses). Nothing in the tests exercises sqlite with realistic payloads, so this is invisible. Fix: serialize with `model_dump(mode="json")` / a proper encoder, and make `set` failures loud in dev.

### 2.3 Yahoo cached history returns a different schema than a fresh fetch
`get_historical_prices` caches `df.to_dict(orient="list")` from the raw yfinance frame — `to_dict` drops the `DatetimeIndex`, so the cached payload has no timestamp at all. Fresh path: index reset → `timestamp` column present. Cache-hit path: no `timestamp` column. This directly violates the "OHLCV schema is guaranteed regardless of source" contract, and the cache-hit test (`test_coverage_push.py::test_yahoo_historical_cache_hit_and_pandas_format`) only asserts `isinstance(df, pd.DataFrame)`, baking the bug in.

### 2.4 `Client()` fails unless every provider is configured
All adapters are constructed eagerly in `AdapterRegistry._build()`, and Fred/AV/Groq/Gemini/Nvidia/Massive/Schwab call `ensure_configured()` in `__init__`, which raises when the API key env var is absent and the provider is enabled (all default to enabled). So a user who only wants Yahoo data cannot construct a `Client` without setting Schwab, Massive, Groq, Gemini, NVIDIA, FRED, and AV credentials or hand-disabling each provider in settings. Your own architecture.md promises the opposite ("Lazy API keys: missing key raises only when that provider's method is called"), and your conftest is the tell: it must monkeypatch nine env vars just to build a Client — and it doesn't set the Schwab ones, which means `Client()` in `test_client.py` should currently raise `FinpipeConfigError` for Schwab. Either the suite is red right now or only passes by accident of ordering; either way the design is wrong. Fix: construct adapters lazily per capability, validate on first use.

### 2.5 Unstable cache keys via `hash()`
`groq.py`/`gemini.py` (`f"groq_{model}_{hash(prompt)}"`) and `massive.py` (`hash(frozenset(filters.items()))`) use Python's salted `hash()`. Keys change every process restart (`PYTHONHASHSEED`), so persistent SQLite caching can never hit across runs, and hash collisions can return the wrong LLM response for a different prompt. Use a content digest (`sha256` of the canonicalized prompt/params).

### 2.6 Optional dependencies aren't optional
`httpx` is an extra (`finpipe[httpx]`) but `network/resilience.py` imports it unconditionally, and `client.py` → `adapter_registry.py` unconditionally imports `yahoo.py` (`import yfinance`), `massive.py` (`import aioboto3`), etc. A base `pip install finpipe` cannot even `import finpipe.client`. Either promote these to core deps or guard imports behind lazy adapter construction (which 2.4 needs anyway).

---

## 3. Security issues

**Secret redaction misses half the secrets.** Both `providers/descriptor.py` and `core/settings_dump.py` redact only `{api_key, access_key_id, secret_access_key}`. Not covered: `app_key`/`app_secret`/`refresh_token` (Schwab), `client_id`/`client_secret` (Reddit). Consequences: `client.catalog.…provider("schwab").describe()` returns the Schwab app secret and refresh token in plaintext; `dump_settings(redact_secrets=True)` returns the Reddit client secret (sentiment sources are included in the dump). The redaction list is duplicated in two files (DRY violation) which is exactly how it drifted. Fix: single shared redaction module, deny-list by suffix (`*_key`, `*_secret`, `*_token`) rather than exact names, and a test asserting no dump contains any configured secret value.

**Bearer/OAuth tokens persisted to disk cache.** Schwab access tokens and Reddit OAuth tokens are stored via the normal cache (`self._cache.set("schwab_access_token", …)`) — with sqlite cache that's plaintext credentials in `finpipe_cache.db`. Tokens should live in memory on the adapter, never in the shared fetch cache.

**Gemini API key in URL.** `{base_url}/{model}:generateContent?key={api_key}` — the key rides in the URL, and `resilience.py` puts the URL into log messages and exception text (`"Circuit breaker tripped for {url}"`). Use the `x-goog-api-key` header.

---

## 4. Architectural drift and design debt

**Two provider registries; one is dead.** `core/registry.py` (decorator registries, `BuildContext`, `Client._ensure_registrations()` side-effect import) exists purely ceremonially — `AdapterRegistry` hardcodes every adapter. Worse, `@register_provider("schwab")` decorates the *class* (all others decorate factories), so `SCHWAB` built through the registry would receive a `BuildContext` where `FinpipeConfig` is expected. Pick one mechanism: either the decorator registry actually drives `AdapterRegistry._build()`, or delete it. Right now the Open/Closed story in your docs (add a provider = register + config) is fiction; adding a provider means editing `AdapterRegistry`, `settings_dump.PROVIDER_NAMES`, `catalog/registry.py`, `ProviderGroupConfig`, and probably `health/probes.py` — five hand-maintained parallel lists (schwab is already missing from `settings_dump.PROVIDER_NAMES` and `CAPABILITY_SETTINGS`).

**Fallback semantics contradict the documented rules.** `call_with_fallback` catches bare `Exception` and moves on to the fallback. architecture.md explicitly says: do not fall back on `FinpipeRateLimitExceededError` (avoids hammering the fallback during throttling), and fall back on *empty* results (empty DataFrame, `None` spot). Neither rule is implemented — rate-limit errors trigger fallback, empty frames don't. Related: `massive.get_options_chain/snapshot` wraps *any* failure (including rate-limit) into `FinpipeDataNotFoundError`, destroying the taxonomy upstream code is supposed to branch on.

**HTTP 4xx handling breaks the taxonomy.** In `ResilientHttpClient.request`, `retry_if_exception_type(httpx.HTTPStatusError)` retries 404s/400s `max_retries` times (pointless, quota-burning) and then maps them to `FinpipeProviderDownError`. The docs' translation table says 404 → `FinpipeDataNotFoundError`. Non-retryable client errors need to be classified before the retry loop. Same overreach in Yahoo's `_execute_with_resilience`: `retry_if_exception_type(Exception)` retries literally everything, including programming errors and "ticker does not exist".

**The catalog handle API sacrifices the type system.** `CapabilityHandle.__getattr__` and `ProviderRef.__getattr__` proxy dynamically to composites/adapters. You've built strict `Protocol`s, `py.typed`, and two type checkers — and then made the only public I/O path (`client.catalog.capability("equity").get_historical_prices(...)`) invisible to all of them: no autocomplete, no signature checking, typos surface at runtime. Either have handles implement the protocols explicitly (thin typed delegation) or restore typed facade attributes (`client.equity: CompositeEquityService`) alongside the catalog for discovery/introspection.

**LLM routing is configured but not implemented.** `RoutingConfig.llm_primary/llm_fallback` exist, the catalog *reports* them, but `Client._composites` has no `"llm"` entry — `capability("llm").generate_response(...)` raises `AttributeError`, and there is no groq→gemini fallback anywhere. Either build `CompositeLlmService` or remove the routing keys; advertising routing that doesn't exist is worse than either.

**TTL semantics diverge from the spec.** architecture.md defines `ttl=0` as "always refetch but still store (for stale-on-rate-limit)" and a whole `allow_stale_on_rate_limit` degradation path. The code has no `allow_stale_on_rate_limit` field, no stale-read path, and `ttl=0` in both backends produces an entry that's expired at birth. Also `YahooTTLConfig.live_spot_price_sec` defaults to 60, while the docs say 0 — with the doc semantics that's a meaningful difference for a "live" price. Yahoo's `get_options_chain` ignores its configured `options_chain_sec` entirely (never cached).

**Namespace mismatches silently disable hard caps.** `get_hard_cap_rps` looks up exact namespace keys. Sentiment clients register as `sentiment.google_news` / `sentiment.reddit` / `sentiment.stocktwits`, but the limits table has `google_news` / `reddit` / `stocktwits` — no match, no clamp. The screener uses `screener.*` keys which do match. Nothing warns on a miss; a typoed namespace silently opts out of vendor safety caps.

**Cache keys ignore the configured namespace.** `ProviderBase.cache_key()` implements the documented `{namespace}:{provider}:{endpoint}` layout — and no adapter uses it. Every adapter hand-rolls keys (`yf_hist_…`, `fred_…`, `av_…`) without `config.cache.namespace`, so the multi-app isolation the docs promise ("aksh:" vs "research:" prefixes) doesn't exist; two apps sharing a cache DB will cross-contaminate. `ProviderBase` itself is near-dead code, and its `cache` *property* calls `resolve_cache_backend` per access — with `singleton: false` that would return a fresh empty cache on every access.

**Constructor side effects everywhere.** Your own handbook: "`__init__` MUST only assign fields and validate." In reality `Client()` synchronously builds 11 adapters, ~20 `AdaptiveRateLimiter`s (each doing `mkdir` + three SQLite connections in `__init__`), several HTTP sessions, and runs a threaded cache stress test (`verify_thread_safe`) — file and DB I/O before the first request. This also makes `Client` construction slow and un-mockable.

**Blocking I/O inside async paths.** `AdaptiveRateLimiter.record_success/record_429` write SQLite synchronously in the event loop on every rate change; all cache `get/set` calls in async adapter methods are synchronous SQLite. Low volume, but it violates the codebase's own ASYNC_SAFETY rule and will bite under load. An async wrapper or `to_thread` for the sqlite backend would fix it.

**Inconsistent error contracts across screener/sentiment.** Yahoo trending/predefined/finviz swallow all exceptions and return `[]` (caller can't distinguish "no matches" from "provider down"), while `run_tradingview` raises `FinpipeProviderDownError`. Pick one contract per capability.

**Alpha Vantage correctness.** `outputsize=compact` returns only ~100 rows; requests with older `start_date` silently return truncated history (cache key `av_hist_{symbol}_{interval}` also ignores the date range, so a "compact" fetch poisons the key for all future ranges). And AV's HTTP-200 soft rate-limit responses (`"Information": …`) never reach `record_429`, so AIMD never backs off for the provider most likely to throttle.

**Duplication (DRY).** `fetch_options_snapshot`/`fetch_options_contracts` are copy-pasted ~120 lines between Yahoo and Schwab; Groq/Gemini/Nvidia adapters are ~85% identical (an `OpenAICompatibleLlmAdapter` base would collapse two of them); `_format_dataframe` is re-implemented five times with subtle differences; secret-redaction logic exists twice.

**Doc drift.** architecture.md still documents `client.equity`-style facades (removed in v0.5.0 per api-reference.md), `ResilienceConfig`/`TTLConfig` shapes that don't exist in `config.py`, transports config (`http2`, `base_url`, `timeout_write_sec`) not present, and a `transports/` module that is an empty `__init__.py`. The "architecture doc must change with src" pre-commit hook clearly isn't holding. Also repo hygiene: `main.py` hello-world scaffold at root, `GeminiPromptCompressionConfig`/`prepare_gemini_prompt` deprecated aliases, `screener_parsers` in `core/` (vendor parsing inside the "pure" core layer), and `scripts/` gitignored while pyproject/pre-commit reference it.

**What the dependency graph confirms (graphify).** The 2026-07-01 graph independently corroborates the structural findings:

- *No import cycles* — the layering discipline is real; credit where due.
- *`FinpipeConfig` is a god node (66 edges, betweenness 0.101, bridging 24 communities).* Root cause: every adapter's constructor takes the **entire** `FinpipeConfig` instead of its own `AbstractProviderConfig` + `CacheConfig`. That violates the codebase's own Interface Segregation and DI rules, makes every adapter recompile-sensitive to any config change, and is why `Client` (77 edges) and `FinpipeConfigError` (52 edges!) are also hubs — an *exception type* being a top-10 most-connected node is the graph's way of saying eager config validation is smeared across every module. Injecting narrow configs would cut most of these edges and fix §2.4 at the same time.
- *Adapters are the next god tier* (`NewsSentimentAdapter` 60, `MassiveOptionsAdapter` 57, `YahooFinanceAdapter` 53) — each adapter owns fetching + parsing + caching + resilience wiring + describe(), which is the SRP drift behind the `_format_dataframe` ×5 duplication.
- *The graph flags the coverage-gaming tests itself*: its "surprising connections" are almost entirely `test_coverage_final/push/95` files reaching into adapters — cross-community edges that behavior-organized tests wouldn't produce.
- *248 isolated nodes* are mostly docs headings and orphaned doc sections — consistent with the architecture-doc drift in this section.

---

## 5. Test suite critique

**Good:** respx-based REST tests (FRED, AV, sentiment) mock at the transport seam — the right level. The AIMD limiter tests are precise (persistence across sessions, 429 decrease, connection-close accounting via a `closing` wrapper, ResourceWarning check). Cache manager singleton tests, config precedence tests, and a gated live-integration tier all exist. Volume is real: ~345 tests across 51 files.

**Coverage gaming.** Six root-level files (`test_coverage_95.py`, `test_coverage_push.py`, `test_coverage_gaps.py`, `test_coverage_final.py`, `test_coverage_last_mile.py`, `test_coverage_edge.py`) plus several `*_coverage.py` twins exist to feed `--cov-fail-under=95`. They're organized by "which lines were still red", not by behavior: they poke private attributes (`adapter._cache.set(...)` then call the method), assert near-nothing (`isinstance(df, pd.DataFrame)`), and duplicate scenarios that belong in the per-module suites. This is how 95% coverage coexists with §2's bugs: the Yahoo cache-hit schema bug and the sqlite serialization failure both sit inside "covered" lines. Meanwhile `pyproject.toml` *omits* `tradingview.py` from coverage entirely — excluding an entire shipped module to make the number is the same pathology.

**Over-mocking hides real breakage.** The Schwab suite replaces `adapter._client` with `AsyncMock` and stubs `_get_access_token`, then asserts against `mock._client.get` — it verifies the adapter calls a method that doesn't exist on the real client. Tests that mock a collaborator should mock its actual interface (`spec=ResilientHttpClient` would have failed immediately). This one line of `autospec` discipline would have caught §2.1.

**Missing tests the docs themselves call mandatory.** No `tests/network/test_cache_concurrency.py` (architecture.md: "must pass in CI, blocks release"), no `tests/contract/` (OHLCV schema/dtype invariants across providers, protocol-surface goldens), no test that sqlite cache round-trips realistic payloads (would have caught §2.2), no test that a fresh fetch and a cache hit return the same schema (would have caught §2.3), no test that `Client()` works with only one provider's credentials (would have caught §2.4), no test that dumps/describes contain no secret values (would have caught §3).

**Structural nit:** mirroring is otherwise decent (`tests/core|network|providers|catalog|health`), fixtures are clean, `asyncio_mode=auto` is used consistently.

---

## 6. Priority recommendations

1. **P0 — correctness:** fix Schwab client calls (or pull the provider until real); make `SqliteCacheBackend` serialize via `mode="json"`+custom encoder and fail loudly in tests; normalize the Yahoo/AV/Fred cache round-trip so cache-hit == fresh schema (cache the *normalized* frame, not the raw one); replace `hash()` cache keys with sha256 digests.
2. **P0 — usability:** lazy adapter construction + first-use `ensure_configured()`, so `Client()` works with only the providers you use. This simultaneously fixes the optional-extras import breakage.
3. **P1 — security:** centralize redaction with suffix-based deny-list + a "no secret values in any dump/describe" test; stop persisting OAuth/bearer tokens in the fetch cache; move the Gemini key to a header.
4. **P1 — taxonomy/resilience:** classify 4xx before retrying (404 → `DataNotFound`, no retry); implement the documented fallback rules (skip fallback on rate-limit, fall back on empty); make Massive stop converting everything to `DataNotFound`; wire AV soft-429s into `record_429`; fix `sentiment.*` namespace keys so hard caps clamp (and warn on lookup miss).
5. **P2 — structure:** inject narrow configs (`AbstractProviderConfig` + `CacheConfig`) into adapters instead of the whole `FinpipeConfig` (the graph's #2 god node); delete or actually use the decorator registry (one registration mechanism, one provider list); extract an OpenAI-compatible LLM base + shared options-snapshot helper + single `format_ohlcv()`; give catalog handles typed protocol implementations; build `CompositeLlmService` or drop `llm_primary/llm_fallback`; adopt `ProviderBase.cache_key()` everywhere so `cache.namespace` isolation is real.
6. **P2 — tests:** replace the coverage-push files with behavior-named tests in the module suites; `spec=`/`autospec` all mocks of internal collaborators; add the contract tier (OHLCV schema goldens, cache round-trip, secret-leak, single-provider Client, cache concurrency); remove the `tradingview.py` coverage omit.
7. **P3 — docs:** reconcile architecture.md with the v0.5 catalog API and the actual config schema, or the "docs must move with src" hook is theater; implement or delete the `allow_stale_on_rate_limit`/TTL-0 semantics.

The bones here are better than most data-pipeline packages — the rate-limiting and config layers in particular are worth keeping intact. The theme to fix is *silent failure*: silent cache no-ops, silent clamp misses, silent `[]` on provider errors, silently dead registries, and a coverage number that silences the test suite. Make failures loud, make construction lazy, and the architecture you documented and the one you shipped will converge.
