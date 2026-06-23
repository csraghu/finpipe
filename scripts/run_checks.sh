#!/usr/bin/env bash
# Run all quality gates locally (same checks as pre-commit).
set -euo pipefail
cd "$(dirname "$0")/.."

python scripts/ensure_typecheck_import_root.py

echo ">> ruff check --fix"
ruff check --fix .

echo ">> ruff format"
ruff format .

echo ">> basedpyright"
basedpyright

echo ">> pyrefly check"
pyrefly check

echo ">> pytest (95% coverage)"
pytest --cov=finpipe --cov-report=term-missing --cov-fail-under=95

echo "All checks passed."
