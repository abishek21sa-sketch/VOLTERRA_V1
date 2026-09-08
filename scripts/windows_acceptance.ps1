$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "PROJECT_ACCEPTANCE=START"
if (Test-Path ".\scripts\verify_release_integrity.py") { py .\scripts\verify_release_integrity.py; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if (Test-Path "pyproject.toml") { & .\.venv\Scripts\python.exe -m pip install -e . } elseif (Test-Path "requirements.txt") { & .\.venv\Scripts\python.exe -m pip install -r requirements.txt }
if (Test-Path ".\ml\pyproject.toml") { & .\.venv\Scripts\python.exe -m pip install -e ".\ml[dev]"; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
if (Test-Path ".\ingestion\pyproject.toml") { & .\.venv\Scripts\python.exe -m pip install -e ".\ingestion[dev]"; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
$env:PYTHONPATH = "$root;$root\src"
& .\.venv\Scripts\python.exe -m compileall -q campaign empirical tenx intelligence scripts
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\run_tenx_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\run_tenx_stress.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\run_airlines15x_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\run_final_depth_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\run_intelligence_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\run_ui_identity_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m pytest -q .\tests\test_tenx_contract.py .\tests\test_airlines15x_empirical.py .\tests\test_project_campaign.py .\tests\test_intelligence_lifecycle.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (Test-Path ".\scripts\windows_native_acceptance.ps1") { & .\scripts\windows_native_acceptance.ps1; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
if (Test-Path ".\scripts\windows_real_data_acceptance.ps1") { & .\scripts\windows_real_data_acceptance.ps1; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
Write-Host "PROJECT_ACCEPTANCE=PASS"
