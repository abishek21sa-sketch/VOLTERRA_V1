$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) { Write-Host "Run scripts\windows_tenx_acceptance.ps1 first."; exit 1 }
& .\.venv\Scripts\python.exe scripts\start_tenx_workstation.py
