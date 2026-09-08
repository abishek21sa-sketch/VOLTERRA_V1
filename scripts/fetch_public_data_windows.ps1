$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -m venv .venv }
$cfg = Get-Content ".\empirical\public_data_config.json" -Raw | ConvertFrom-Json
if ($null -ne $cfg.uci_id) { & .\.venv\Scripts\python.exe -m pip install --upgrade ucimlrepo pandas }
& .\.venv\Scripts\python.exe .\scripts\fetch_public_data.py @args
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\build_data_backbone.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "PUBLIC_DATA_WINDOWS_REFRESH=PASS"
