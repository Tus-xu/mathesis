# Docling 离线权重下载（HF snapshot_download SSL 断流时的绕法）
# 用法: powershell -File scripts/fetch-docling-models.ps1
#
# 落地到 Docling artifacts_path 约定：
#   .parser-cache/artifacts/docling-project--docling-layout-heron/
#   .parser-cache/artifacts/docling-project--docling-models/model_artifacts/tableformer/{accurate,fast}/
param(
  [string]$Root = (Split-Path -Parent $PSScriptRoot),
  [string]$Endpoint = 'https://hf-mirror.com'
)
$ErrorActionPreference = 'Continue'
$art = Join-Path $Root '.parser-cache\artifacts'

function Get-HFFile($repo, $revision, $rel, $out, $endpoint) {
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $out) | Out-Null
  $url = "$endpoint/$repo/resolve/$revision/$rel"
  curl.exe -k -sS -L --retry 2 --retry-delay 2 --max-time 600 -o $out $url | Out-Null
  if ((Test-Path $out) -and (Get-Item $out).Length -gt 100) {
    Write-Host ("OK  {0} ({1} MB)" -f $rel, [math]::Round((Get-Item $out).Length / 1MB, 2))
    return $true
  }
  Write-Host "MISS $rel"
  return $false
}

# 1) layout-heron
$layout = Join-Path $art 'docling-project--docling-layout-heron'
Get-HFFile 'docling-project/docling-layout-heron' 'main' 'config.json' (Join-Path $layout 'config.json') $Endpoint | Out-Null
Get-HFFile 'docling-project/docling-layout-heron' 'main' 'model.safetensors' (Join-Path $layout 'model.safetensors') $Endpoint | Out-Null

# 2) TableFormer (docling-models v2.3.0)
$tf = Join-Path $art 'docling-project--docling-models\model_artifacts\tableformer'
Get-HFFile 'docling-project/docling-models' 'v2.3.0' 'model_artifacts/tableformer/accurate/tableformer_accurate.safetensors' (Join-Path $tf 'accurate\tableformer_accurate.safetensors') $Endpoint | Out-Null
Get-HFFile 'docling-project/docling-models' 'v2.3.0' 'model_artifacts/tableformer/accurate/tm_config.json' (Join-Path $tf 'accurate\tm_config.json') $Endpoint | Out-Null
Get-HFFile 'docling-project/docling-models' 'v2.3.0' 'model_artifacts/tableformer/fast/tableformer_fast.safetensors' (Join-Path $tf 'fast\tableformer_fast.safetensors') $Endpoint | Out-Null
Get-HFFile 'docling-project/docling-models' 'v2.3.0' 'model_artifacts/tableformer/fast/tm_config.json' (Join-Path $tf 'fast\tm_config.json') $Endpoint | Out-Null

Write-Host '--- sha256 ---'
Get-ChildItem $art -Recurse -File -Filter '*.safetensors' | ForEach-Object {
  Write-Host ("{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name)
}
