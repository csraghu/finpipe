# Run all quality gates locally (same checks as pre-commit).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host ">> ruff check --fix"
ruff check --fix .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ">> ruff format"
ruff format .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ">> basedpyright"
basedpyright
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ">> pyrefly check"
pyrefly check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ">> pytest (95% coverage)"
pytest --cov=finpipe --cov-report=term-missing --cov-fail-under=95
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "All checks passed."
