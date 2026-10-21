# 本机离线跑 Standard（已验证 USERPROFILE 绕过）的推荐启动方式
# 不依赖 Docker pip；直接用 .parser-venv + ASCII USERPROFILE
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)  # services/research-vnext
$pv = Join-Path $root '.parser-venv\Scripts\python.exe'
$cache = Join-Path $root '.parser-cache'
$user = Join-Path $cache 'userprofile'
New-Item -ItemType Directory -Force -Path $user, "$user\AppData\Local", "$user\AppData\Roaming" | Out-Null
$env:PYTHONUTF8 = '1'
$env:USERPROFILE = $user
$env:HOMEDRIVE = 'D:'
$env:HOMEPATH = '\math-arena\services\research-vnext\.parser-cache\userprofile'
$env:LOCALAPPDATA = "$user\AppData\Local"
$env:APPDATA = "$user\AppData\Roaming"
$env:TMP = $cache; $env:TEMP = $cache; $env:TMPDIR = $cache
$env:HF_HOME = Join-Path $cache 'hf'
$env:DOCLING_CACHE = Join-Path $cache 'docling'
$env:DOCLING_ARTIFACTS = Join-Path $cache 'artifacts'
$env:DOCLING_PIPELINE = 'standard'   # native | standard
$env:RESEARCH_PARSER_PYTHON = $pv
$env:RESEARCH_PARSER_ENGINE = 'docling'
Write-Host "parser=$pv"
Write-Host "USERPROFILE=$env:USERPROFILE"
Write-Host "PIPELINE=$env:DOCLING_PIPELINE ARTIFACTS=$env:DOCLING_ARTIFACTS"
if ($args.Count -ge 1) {
  & $pv $args
}
