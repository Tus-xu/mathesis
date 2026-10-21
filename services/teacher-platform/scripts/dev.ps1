# 教师端 V3 平台 · Windows 开发启动壳（等价 make up）
# 若 Docker 拉取超时：在 Docker Desktop → Settings → Docker Engine 的 daemon.json 增加
#   "registry-mirrors": ["https://docker.m.daocloud.io", "https://dockerproxy.net"]
# 然后 Apply & Restart。

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> docker compose up -d --build"
docker compose up -d --build

Write-Host "==> 等待 /healthz 全绿（≤3 分钟）"
for ($i = 0; $i -lt 60; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8100/healthz" -TimeoutSec 3 -UseBasicParsing
        if ($r.StatusCode -eq 200) { Write-Host "==> /healthz 全绿 ✔"; exit 0 }
    } catch { Start-Sleep -Seconds 3 }
}
Write-Host "==> /healthz 未全绿，请查看: docker compose ps"; exit 1
