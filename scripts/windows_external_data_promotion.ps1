$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
Write-Host "EXTERNAL_DATA_REFRESH=START"
& .\data\external\fetch_public_data.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python .\scripts\run_external_data_promotion.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python .\scripts\run_airlines15x_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "EXTERNAL_DATA_PROMOTION=PASS"
