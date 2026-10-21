# dev-down.ps1 - Stop the math-arena backend listening on port 8000.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\dev-down.ps1

$conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if (-not $conns) {
    Write-Host 'No listener on port 8000.'
    exit 0
}
$conns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object {
    Stop-Process -Id $_ -Force
    Write-Host "Stopped PID $_"
}
