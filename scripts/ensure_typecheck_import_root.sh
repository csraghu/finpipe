#!/usr/bin/env bash
# Junction/symlink src/ as ./finpipe for static analyzers (see ensure_typecheck_import_root.py).
set -euo pipefail
cd "$(dirname "$0")/.."
python scripts/ensure_typecheck_import_root.py
