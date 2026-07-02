# finpipe v2 — Rearchitecture Blueprint

Companion to `docs/architecture-review.md` (2026-07-01). Goal: keep what works (layering, protocols, AIMD stack, config model) and eliminate the systemic drawbacks — silent cache failure, eager construction, god-node coupling on `FinpipeConfig`, broken exception taxonomy, secret leakage, parallel hand-maintained provider lists, and an untyped public API.

**Isolation strategy:** all new code lives in `v2/finpipe/` on a `rearch` branch. Nothing under `src/` changes until the final cutover phase, so the current flow (including the application layer) keeps running throughout. Schwab is out of scope per decision.

---

## 1. Design principles (each maps to a review finding)

1. **Narrow dependency injection.** No adapter ever sees `FinpipeConfig`. Each adapter receives a `ProviderRuntime` — its own provider config, a namespaced cache view, and a request executor. This dissolves the `FinpipeConfig` god node (66 edges) and the `FinpipeConfigError` hub, and makes adapters unit-testable with three small fakes.
2. **Lazy everything, loud validation.** `Client()` performs zero I/O and zero credential checks. Adapters are built on first use per capability; `ensure_configured()` runs then, raising a `FinpipeConfigError` that names exactly the missing env var. A client configured only for Yahoo works with no other keys. This also fixes the optional-extras problem: `import yfinance` happens inside the Yahoo factory, not at package import.
3. **One source of truth per fact.** A single provider *manifest* (decorator registry entry) carries: registry key, capability, config binding, catalog metadata, health-probe key, required secrets, optional extra name. Adapter construction, catalog, settings dump, health probes, and docs tables are all *derived* from it. Adding a provider = one module + one config block; forgetting a parallel list becomes impossible.
4. **Correct-by-construction caching.** One `Codec` serializes every cacheable type (datetime/date, `pd.Timestamp`, DataFrames via a canonical envelope) — round-trip tested. Cache writes that fail raise in strict mode (tests/dev) and log-once in production. Keys are `sha256` digests over canonicalized params, always prefixed `{cache_namespace}:{provider}:{endpoint}:`. Adapters cache the **normalized** value (post-parsing), never the raw vendor frame, so cache-hit output is byte-identical to fresh output by design. Cache access is async (`to_thread` for SQLite).
5. **Exception taxonomy decided in one place.** A `classify(status, exc)` step runs *before* retry: 400/404 → `FinpipeDataNotFoundError` (never retried), 401/403 → `FinpipeConfigError`/auth (never retried), 429 → `FinpipeRateLimitExceededError` (AIMD backoff, bounded retry), 5xx/network → retried then `FinpipeProviderDownError`. Adapters may *narrow* an error, never re-map it.
6. **Fallback policy as data, not scattered try/except.** The composite services consult one `FallbackPolicy`: fall back on `DataNotFoundError`, `ProviderDownError`, and empty results; **never** on `RateLimitExceededError` or `ConfigError`. Policy is unit-tested once, used by all capabilities — and LLM finally gets its composite (groq → gemini per `routing.llm_*`).
7. **Secrets are types, not strings.** All credentials are `pydantic.SecretStr`. Redaction is by type (any `SecretStr`) plus a suffix deny-list (`*_key`, `*_secret`, `*_token`) — leaking a new secret requires actively working around the type system. OAuth/bearer tokens live in an in-memory `TokenStore` on the adapter, never in the fetch cache. API keys go in headers, never URLs; URLs in logs/exception text are sanitized.
8. **Typed public API.** `client.equity`, `client.options`, `client.macro`, `client.intel`, `client.screener`, `client.llm` return concrete service classes that *implement the protocols* — full autocomplete and type-checking. `client.catalog` remains, but as **introspection only** (inventory, `describe()`, health template), no `__getattr__` proxying for I/O.
9. **No blocking I/O on the event loop.** SQLite cache and AIMD rate persistence go through `asyncio.to_thread`; AIMD saves are debounced (persist at most every N seconds or on shutdown) instead of on every ±0.5 RPS change.
10. **Uniform degradation contract per capability.** Within a capability, sources either all raise or all return typed empty results with a `warnings` channel — no more finviz-returns-`[]` vs tradingview-raises.

---

## 2. Target layout (`v2/finpipe/`)

```text
v2/
└── finpipe/
    ├── __init__.py            # Client, FinpipeConfig, errors, models (semver surface)
    ├── client.py              # thin facade; lazy capability services; zero-I/O ctor
    ├── core/
    │   ├── config.py          # FinpipeConfig + per-provider configs (SecretStr creds)
    │   ├── errors.py          # Finpipe*Error (unchanged hierarchy)
    │   ├── models.py          # Pydantic DTOs (unchanged shapes)
    │   ├── protocols.py       # capability Protocols (unchanged intent)
    │   └── redact.py          # single redaction implementation (type + suffix based)
    ├── runtime/               # infrastructure, no provider knowledge
    │   ├── codec.py           # canonical serialization for cacheable types
    │   ├── cache.py           # async CacheBackend protocol; Memory/Sqlite; NamespacedCache
    │   ├── ratelimit.py       # AIMD + RpmTpm + concurrency (debounced async persistence)
    │   ├── transport.py       # Transport protocol; HttpxTransport; CurlCffiTransport
    │   ├── resilience.py      # classify() + RequestExecutor (limits→breaker→retry→classify)
    │   └── tokens.py          # in-memory TokenStore (OAuth/bearer, never disk)
    ├── providers/
    │   ├── manifest.py        # ProviderManifest + @provider decorator + registry (THE list)
    │   ├── base.py            # ProviderRuntime, ProviderAdapter, cached_fetch()
    │   ├── normalize.py       # ONE format_ohlcv()/format_macro() (schema-enforcing)
    │   ├── yahoo.py fred.py alpha_vantage.py massive.py
    │   ├── sentiment.py screener.py
    │   └── llm/
    │       ├── base.py        # prompt prep + shared generate flow
    │       ├── openai_compat.py  # Groq + NVIDIA (one class, two manifests)
    │       └── gemini.py
    ├── capabilities/
    │   ├── policy.py          # FallbackPolicy (the only fallback logic in the package)
    │   ├── equity.py options.py macro.py intel.py screener.py llm.py
    ├── observe/
    │   ├── catalog.py         # derived from manifests (introspection only)
    │   ├── health.py          # probes derived from manifests
    │   └── settings_dump.py   # derived from manifests + core.redact
    └── py.typed
```

Dependency rule (enforced with an import-linter contract in CI): `core` ← `runtime` ← `providers` ← `capabilities` ← `client`/`observe`. `runtime` imports nothing from `providers`.

### Key contracts

```python
# providers/base.py
@dataclass(frozen=True)
class ProviderRuntime:
    config: AbstractProviderConfig      # THIS provider's block only
    cache: NamespacedCache              # keys pre-prefixed; async get/set
    executor: RequestExecutor           # rate-limit + breaker + retry + classify
    dataframe_format: DataFrameFormat   # the one global knob adapters need

class ProviderAdapter:
    def __init__(self, runtime: ProviderRuntime): ...
    async def cached_fetch(self, endpoint: str, params: Mapping, ttl_s: float,
                           fetch: Callable[[], Awaitable[T]]) -> T:
        """digest key → async cache get → fetch → normalize → cache set (strict codec) → return"""
```

```python
# providers/manifest.py — the single provider list
@provider(
    key="fred", capability="macro", config_attr="fred",
    secrets=("FRED_API_KEY",), extra=None, probe="macro.fred",
    label="FRED", description="St. Louis Fed macro series",
)
def build_fred(rt: ProviderRuntime) -> FredAdapter: ...
```

```python
# runtime/resilience.py — taxonomy in one place
class RequestExecutor:
    async def request(self, method, url, *, retryable=True, token_estimate=None, **kw) -> Response:
        # acquire limits → concurrency → transport → classify(status) →
        # 429: record_429 + bounded retry;  5xx/network: retry;  4xx: raise immediately
```

```python
# capabilities/policy.py — fallback in one place
FALLBACK_ON = (FinpipeDataNotFoundError, FinpipeProviderDownError)
NEVER_FALLBACK_ON = (FinpipeRateLimitExceededError, FinpipeConfigError)
def is_empty(result) -> bool: ...   # empty frame / None / empty chain → try fallback
```

---

## 3. What is deliberately kept

The exception hierarchy, the Pydantic DTO shapes, the capability protocol signatures, the AIMD algorithm and its constants, the hard-cap clamp table (with a **warning on namespace miss** added), the settings-file discovery/merge/env-override logic, the health probe model, and the manifest-driven descendants of the catalog. v2 is a re-plumbing, not a rewrite of the domain design — the review found the *design* sound.

---

## 4. Phased migration (old flow untouched until Phase 6)

| Phase | Scope | Exit gate |
|-------|-------|-----------|
| **0** | `git checkout -b rearch`; scaffold `v2/finpipe/` (this commit). Old `src/` untouched; `v2/` not yet packaged. | skeleton imports cleanly |
| **1** | `runtime/`: codec, async cache (memory+sqlite), ratelimit port, transports, `RequestExecutor` with `classify()` | contract tests: codec round-trips `datetime`/`Timestamp`/DataFrame; cache strict-mode raises on unserializable; 404 not retried; 429 backs off; sqlite via `to_thread` |
| **2** | `core/`: config with `SecretStr`, `redact.py`; `providers/manifest.py` + `base.py` + `normalize.py` | secret-leak scan test (no dump/describe contains a secret value); one `format_ohlcv()` schema golden |
| **3** | Reference providers: **FRED** (pure httpx) and **Yahoo** (sync-bridge) + `capabilities/macro.py`, `equity.py`, `policy.py` | fresh-vs-cached equality test (same schema, same values); single-provider `Client()` works with only Yahoo |
| **4** | Remaining providers: Alpha Vantage (fix `outputsize`/date-range key), Massive (digest keys, honest taxonomy), sentiment, screener (uniform degradation), LLM tree incl. `CompositeLlmService` | per-provider respx suites with `spec=` mocks; AV soft-429 feeds `record_429` |
| **5** | `observe/` (catalog/health/dump derived from manifests), typed `client.py`, `v2` README + api-reference rewrite | catalog/health/dump parity tests generated from manifests |
| **6** | **Cutover:** switch `pyproject` `package-dir` `finpipe = "v2/finpipe"`; port the application layer; move `src/` → `legacy/` for one release, then delete. Port only tests worth keeping (behavior-named); drop all `test_coverage_*` files; coverage measured on v2 without omits | app-layer regression green on v2; old path deleted |

Rules during migration: `v2/` never imports from `src/` (and vice versa); every phase lands with its tests; the review's P0/P1 items are structurally impossible to reintroduce (strict codec, taxonomy-in-executor, manifest-derived lists, SecretStr).

---

## 5. Test strategy for v2

Behavior-first suites mirroring the package tree, plus a **contract tier** that locks the invariants the old suite missed: OHLCV/macro schema goldens across providers; cache round-trip equivalence (fresh == cached, both backends); secret-leak scan over every dump/describe payload; taxonomy table test (status code → exception type → retried? → fallback?); single-provider client construction; cache-concurrency (async tasks + threads on sqlite). All mocks of internal collaborators use `spec=`/`autospec`. Coverage gate stays, but with no module omits and no line-hunting files — if a line needs a test, the test gets a behavior name and lives in the right suite.

---

## 6. Risks and mitigations

DataFrame round-trip fidelity through the codec is the trickiest bit — mitigate with property-style tests over dtypes (datetime tz, int64/float64, nulls) before any provider migrates. The sandbox in this workspace couldn't execute tests at planning time, so Phase 1 must start by running the contract tests locally (`uv run pytest v2/tests`). OneDrive + SQLite WAL don't mix well — keep cache/rate DBs out of the synced tree by defaulting to a local app-data path.
