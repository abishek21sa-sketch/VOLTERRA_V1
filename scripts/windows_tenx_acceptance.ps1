$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if (Test-Path "pyproject.toml") { & .\.venv\Scripts\python.exe -m pip install -e . }
elseif (Test-Path "requirements.txt") { & .\.venv\Scripts\python.exe -m pip install -r requirements.txt }
elseif (Test-Path "ml\pyproject.toml") { & .\.venv\Scripts\python.exe -m pip install numpy }
& .\.venv\Scripts\python.exe scripts\run_tenx_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe scripts\run_tenx_stress.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "AIRLINES15X_EMPIRICAL_GATE"
& .\.venv\Scripts\python.exe scripts\run_airlines15x_validation.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "FORTUNE50_TENX_ACCEPTANCE=PASS"
