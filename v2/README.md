# finpipe v2 (rearch branch)

Rearchitecture per `docs/rearchitecture-plan.md`, addressing every finding in
`docs/architecture-review.md` (Schwab excluded per decision). **Not packaged yet** —
root `pyproject.toml` still points at `src/`, so the current v1 flow is untouched.
Nothing in `v2/` imports from `src/`, and vice versa.

## Morning checklist

The shell sandbox was unavailable when this was written, so the code is complete
but **unexecuted**. First actions:

```powershell
# 1. (Optional) fix the Cowork sandbox: quit Claude app, delete
#    %APPDATA%\Claude\vm_bundles, restart the app.

# 2. Run the v2 suite (own pytest.ini — do NOT run from repo root,
#    the root config applies v1 coverage gates):
cd v2
..\.venv\Scripts\python.exe -m pytest -q

# 3. Lint/typecheck if desired:
..\.venv\Scripts\python.exe -m ruff check finpipe
```

Expect small breakages (imports, pydantic quirks) — the architecture is settled;
fixes should be mechanical. The retry tests sleep real backoff (~5–10 s total).

## What is implemented

| Layer | Modules | Review findings fixed |
|-------|---------|----------------------|
| runtime | `codec` (strict envelope: datetime/Timestamp/DataFrame/NaN), `cache` (async, memory/sqlite, `NamespacedCache`, singleton manager, `get_stale`), `ratelimit` (AIMD port, debounced off-loop persistence, hard-cap warn-on-miss + leaf-name fallback), `transport` (httpx/curl_cffi seam), `resilience` (`classify()` taxonomy, selective breaker, `execute()` for sync-bridge vendors), `tokens` (in-memory OAuth store), `paths` (state outside OneDrive) | §2.2 silent cache no-op, §2.5 `hash()` keys, §4 taxonomy/404-retry/blocking-I/O/namespace-clamp, §3 tokens-on-disk |
| core | `config` (SecretStr creds, no eager validation, discovery/merge/env kept), `errors` (+`FinpipeAuthError`), `models`, `protocols`, `redact` (single impl, type+suffix) | §2.4 eager Client, §3 redaction gaps |
| providers | `manifest` (single `@provider` registry driving everything), `base` (`ProviderRuntime` narrow DI + `cached_fetch` normalizes-then-caches), `normalize`, `snapshots`, `wiring` (lazy `AdapterPool`), adapters: fred, yahoo, alpha_vantage (outputsize + soft-429→AIMD), massive (honest taxonomy, digest keys), sentiment (TokenStore), screener (uniform contract), `llm/` (shared base, openai_compat = groq+nvidia, gemini key-in-header) | §2.1 pattern, §2.3 cache-hit schema, §4 dead registry/parallel lists/DRY, graphify god-node |
| capabilities | `policy` (fallback rules as data), typed `equity/options/macro/intel/screener/llm` services — **LLM routing now exists** | §4 fallback-on-rate-limit, missing LLM composite, untyped catalog I/O |
| client / observe | zero-I/O `Client` with typed lazy services; `catalog` (introspection-only), `health`, `settings_dump` — all derived from manifests | §2.4, §4 five parallel provider lists |
| tests | `v2/tests/` — codec round-trips, cache strict/TTL-0/namespacing/sqlite-datetime, taxonomy table, breaker, fallback policy, FRED reference (fresh==cached), AV outputsize/soft-429, LLM digest-cache/header-key, zero-credential Client, secret-leak dump scan | the contract tier v1 never had |

## Deliberately deferred (next phases)

- Yahoo adapter tests (need `yfinance` importable; adapter is written)
- Massive/sentiment/screener adapter test suites (patterns identical to FRED/AV)
- Rich per-source health probes (frame is in `observe/health.py`)
- packaging cutover: `[tool.setuptools.package-dir] finpipe = "v2/finpipe"`, port
  app layer, retire `src/` (plan Phase 6)
- import-linter layering contract in CI
