# dev-up.ps1 - Start math-arena backend (uvicorn) detached from any console.
#
# Why: uvicorn started from a terminal/CLI is a child of that terminal; when the
# terminal or host session closes, Windows kills the whole process group (clients
# see WinError 10054). This script creates the process via WMI
# (Win32_Process.Create) so it is owned by the WMI service, not by any terminal.
#
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev-up.ps1

$ErrorActionPreference = 'Stop'
$apiDir = 'D:\math-arena\services\api'
$python = Join-Path $apiDir '.venv\Scripts\python.exe'
$logDir = Join-Path $apiDir 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$out = Join-Path $logDir 'dev-server.log'
$err = Join-Path $logDir 'dev-server.err.log'

# Already running?
$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    $pidFound = ($existing | Select-Object -First 1).OwningProcess
    Write-Host "Port 8000 already listening (PID $pidFound), nothing to do."
    exit 0
}

# cmd /c only provides >> log redirection; the process itself is created by WMI.
$cmd = "cmd.exe /c `"`"$python`" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >> `"$out`" 2>> `"$err`"`""
$result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
    -Arguments @{ CommandLine = $cmd; CurrentDirectory = $apiDir }
if ($result.ReturnValue -ne 0) {
    Write-Host "Start failed, ReturnValue=$($result.ReturnValue)"
    exit 1
}
Write-Host "uvicorn started (PID $($result.ProcessId)), log: $out"

# Poll health endpoint (max 30s)
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2 | Out-Null
        Write-Host 'Health check OK: http://127.0.0.1:8000/api/health'
        exit 0
    } catch {}
}
Write-Host "Health check timed out, see log: $err"
exit 1
