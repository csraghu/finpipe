# Session handoff — finpipe rearchitecture (written 2026-07-02)

Context file for continuing work in a new Cowork session. Read this first, then
the three documents below.

## State of the repo

- **Branch:** user created `rearch` for the v2 work (verify with `git branch`).
  v1 (`src/`) is untouched and still the packaged code; the running application
  flow must keep working until Phase 6 cutover.
- **`docs/architecture-review.md`** — full architectural review of v1: what's
  good, critical defects (silent SQLite cache no-op, cache-hit schema drift,
  eager Client validation, salted-hash cache keys, secret leaks in
  describe/dump, broken exception taxonomy, fallback-on-rate-limit, dead
  registry, coverage-gamed tests). Schwab adapter is broken but explicitly
  OUT OF SCOPE per user decision.
- **`docs/rearchitecture-plan.md`** — the v2 blueprint: 10 design principles,
  target layout, 6 migration phases.
- **`v2/`** — the full v2 implementation (~34 files) + its own test suite and
  `pytest.ini`. See `v2/README.md` for the module→fix mapping and the morning
  checklist. v2 never imports from `src/` and vice versa.

## Critical caveat

**The v2 code has NEVER been executed.** The previous session's Linux sandbox
was broken (EXDEV VM boot failure — since fixed by an app restart). The
architecture is settled; expect mechanical failures (imports, pydantic quirks),
not structural ones.

## Immediate next steps (in order)

1. `cd v2 && python -m pytest -q` (use the repo venv: `..\.venv\Scripts\python.exe -m pytest -q`
   from `v2/`, or the sandbox equivalent). Do NOT run pytest from the repo root —
   the root `pyproject.toml` applies v1's coverage gates and typecheck paths.
2. Fix failures until green. Keep the test intent intact — the tests encode the
   review's contract fixes (fresh==cached, taxonomy table, secret-leak scan,
   zero-credential Client).
3. `ruff check v2/finpipe` and fix lint.
4. Commit to `rearch`: `v2/` + `docs/architecture-review.md` +
   `docs/rearchitecture-plan.md` + this file.
5. Then continue the plan's Phase 4/5 leftovers (listed in `v2/README.md`
   "Deliberately deferred"): Yahoo/massive/sentiment/screener test suites,
   richer health probes, packaging cutover LAST (Phase 6).

## Decisions already made (do not re-litigate)

- Breaking the v1 public API is fine; work stays isolated in `v2/` until the
  whole flow incl. application layer is ported (user requirement).
- Schwab: ignored for now.
- Secrets: `SecretStr` + suffix redaction; tokens in memory only; keys in
  headers, never URLs.
- Fallback policy: on not-found/provider-down/empty; NEVER on rate-limit or
  config errors.
- SQLite state (cache + learned rates) defaults OUTSIDE the OneDrive tree
  (`FINPIPE_STATE_DIR`, see `v2/finpipe/runtime/paths.py`).
