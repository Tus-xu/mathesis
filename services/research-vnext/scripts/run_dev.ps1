<#
.SYNOPSIS
  Start research-vnext (8300) with production semantics.

.DESCRIPTION
  This machine has no `uv`, so the service's own isolated venv is used:
      <service root>\.venv

  The wrapper passes already-set process environment variables through to uvicorn
  untouched; anything missing is filled in from `.env` at app import time by
  research_vnext.core.bootstrap_env (explicit environment variables always win,
  so local defaults can be overridden per-invocation).

  RESEARCH_ENV defaults to `prod`, which faithfully reflects production: startup
  only VALIDATES that the schema was created by `alembic upgrade head`, and
  exits if it was not. There is deliberately no runtime-DDL fallback.

.NOTES
  ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less script files as
  ANSI, which mangles non-ASCII comments into parse errors.

  Secrets are never printed: only the presence and length of
  RESEARCH_GATEWAY_HMAC_SECRET are reported.
#>
[CmdletBinding()]
param(
  [string]$BindHost = "127.0.0.1",
  [int]$Port = 8300,
  [string]$Schema = "rv8300",
  [string]$EnvName = "prod",
  [switch]$Reload
)

$ErrorActionPreference = "Stop"

$serviceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$py = Join-Path $serviceRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  throw "Missing venv interpreter: $py . Create it first (see README section 'Run')."
}

# --- environment handed to the child process --------------------------------
if (-not $env:RESEARCH_DATABASE_URL) {
  $env:RESEARCH_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:54329/research_vnext"
}
$env:RESEARCH_DB_SCHEMA = $Schema
$env:RESEARCH_ENV = $EnvName
$env:PYTHONPATH = $serviceRoot
if (-not $env:RESEARCH_SSE_HEARTBEAT_SECONDS) {
  $env:RESEARCH_SSE_HEARTBEAT_SECONDS = "15"
}

$envFile = Join-Path $serviceRoot ".env"
if (-not (Test-Path $envFile)) {
  throw "Missing $envFile . Copy .env.example and fill in RESEARCH_GATEWAY_HMAC_SECRET."
}

# --- preflight report: facts only, never secret values ----------------------
Write-Host "[run_dev] service root : $serviceRoot"
Write-Host "[run_dev] python       : $py"
Write-Host "[run_dev] bind         : ${BindHost}:${Port}"
Write-Host "[run_dev] schema       : $env:RESEARCH_DB_SCHEMA"
Write-Host "[run_dev] RESEARCH_ENV : $env:RESEARCH_ENV"
Write-Host "[run_dev] db target    : $($env:RESEARCH_DATABASE_URL.Split('@')[-1])"
Write-Host "[run_dev] .env present : $(Test-Path $envFile)"
Write-Host "[run_dev] secret set   : $([bool]$env:RESEARCH_GATEWAY_HMAC_SECRET) (length=$($env:RESEARCH_GATEWAY_HMAC_SECRET.Length))"
if (-not $env:RESEARCH_GATEWAY_HMAC_SECRET) {
  Write-Warning "RESEARCH_GATEWAY_HMAC_SECRET is not set in the calling environment; .env will supply it. If .env is empty too, every request returns 401 (fail closed)."
}

$uvicornArgs = @(
  "-m", "uvicorn", "research_vnext.core.api.app:app",
  "--host", $BindHost, "--port", "$Port"
)
if ($Reload) { $uvicornArgs += @("--reload") }

Push-Location $serviceRoot
try {
  & $py @uvicornArgs
} finally {
  Pop-Location
}
