$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "AIRLINES15X_ACCEPTANCE=START"
if (Test-Path ".\scripts\verify_release_integrity.py") {
  py .\scripts\verify_release_integrity.py
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if (Test-Path "pyproject.toml") { & .\.venv\Scripts\python.exe -m pip install -e . }
elseif (Test-Path "requirements.txt") { & .\.venv\Scripts\python.exe -m pip install -r requirements.txt }
if (Test-Path ".\ml\pyproject.toml") { & .\.venv\Scripts\python.exe -m pip install numpy pytest; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
& .\.venv\Scripts\python.exe -m compileall -q empirical tenx scripts
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\run_tenx_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\run_tenx_stress.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe .\scripts\run_airlines15x_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m pytest -q .\tests\test_tenx_contract.py .\tests\test_airlines15x_empirical.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "AIRLINES15X_ACCEPTANCE=PASS"
