# RV08 helper: start research-vnext (8300) with LLM credentials injected from the
# repository-root .env. Secret VALUES are never printed; only presence/length.
#
# Why this exists: run_dev.ps1 reads the *service* .env, which has no LLM keys,
# so prod-mode startup aborts with LlmGateError(LLM_NOT_CONFIGURED).
[CmdletBinding()]
param(
  [int]$Port = 8300,
  [string]$Schema = "rv8300"
)

$ErrorActionPreference = "Stop"

$repoRoot = "D:\math-arena"
$envFile = Join-Path $repoRoot ".env"
if (-not (Test-Path $envFile)) { throw "Missing $envFile" }

# Load key=value pairs into the process environment (no echoing of values).
foreach ($line in Get-Content $envFile) {
  $trimmed = $line.Trim()
  if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
  $idx = $trimmed.IndexOf("=")
  if ($idx -lt 1) { continue }
  $name = $trimmed.Substring(0, $idx).Trim()
  $value = $trimmed.Substring($idx + 1).Trim().Trim('"').Trim("'")
  if (-not $name -or -not $value) { continue }
  # Explicit process env wins: do not clobber anything already set.
  if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
  }
}

Write-Host "[rv08-launch] injected keys from root .env"
Write-Host "[rv08-launch] SPARK set  : $([bool]$env:SPARK_API_PASSWORD)"
Write-Host "[rv08-launch] schema     : $Schema"
Write-Host "[rv08-launch] port       : $Port"

& powershell -NoProfile -ExecutionPolicy Bypass -File "D:\math-arena\services\research-vnext\scripts\run_dev.ps1" -Port $Port -Schema $Schema
