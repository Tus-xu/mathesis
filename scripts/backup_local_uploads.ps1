# backup_local_uploads.ps1 - Snapshot-backup the dev local file store (data/file-uploads).
#
# Why: dev environment stores uploaded photos (error-book originals, OCR sources) as
# plain files under data/file-uploads. That directory lives inside the repo working
# tree, so a workspace cleanup (git clean / disk cleanup) silently breaks every
# local: file row in the DB - the exact cause of the 2026-09-02 broken error-book
# images. This script copies the directory OUTSIDE the repo (default
# D:\math-arena-backups) into a timestamped folder and prunes old snapshots.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup_local_uploads.ps1
#   powershell ... -BackupRoot D:\other\backups -Keep 20   # custom target / retention

param(
    [string]$BackupRoot = 'D:\math-arena-backups',
    [int]$Keep = 20
)

$ErrorActionPreference = 'Stop'
$src = Join-Path $PSScriptRoot '..\data\file-uploads'
if (-not (Test-Path $src)) {
    Write-Host "No local uploads found at $src (nothing to back up)."
    exit 0
}

New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$dest = Join-Path $BackupRoot "file-uploads-$stamp"
Copy-Item -Path $src -Destination $dest -Recurse
$count = (Get-ChildItem -Path $dest -Recurse -File | Measure-Object).Count
Write-Host "Backed up $count files -> $dest"

# Retention: keep the newest $Keep timestamped snapshots, drop older ones.
$old = Get-ChildItem -Path $BackupRoot -Directory -Filter 'file-uploads-*' |
    Sort-Object Name -Descending | Select-Object -Skip $Keep
foreach ($d in $old) {
    Remove-Item -Path $d.FullName -Recurse -Force
    Write-Host "Pruned old snapshot $($d.Name)"
}
