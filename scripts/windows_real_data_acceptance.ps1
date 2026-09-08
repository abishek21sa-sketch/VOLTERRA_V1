$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = if (Test-Path ".venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "py" }
& $py .\scripts\build_data_backbone.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $py -m pytest -q .\tests\test_real_data_backbone.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "REAL_DATA_ACCEPTANCE=PASS"
