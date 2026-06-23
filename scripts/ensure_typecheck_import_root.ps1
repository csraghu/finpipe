# Junction src/ as ./finpipe so static analyzers resolve `finpipe.*` imports.
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
python (Join-Path $RepoRoot "scripts\ensure_typecheck_import_root.py")
