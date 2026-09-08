$ErrorActionPreference = "Stop"
Write-Host "VOLTERRA Enterprise Acceptance"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -3 -m venv .venv }
$python = ".venv\Scripts\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install scipy pytest
$env:PYTHONPATH = $root
& $python -m pytest -q validation/tests
if ($LASTEXITCODE -ne 0) { throw "VOLTERRA reference formulation tests failed" }
& $python validation/run_portfolio_validation.py
& $python validation/run_enterprise_operability.py
if (-not (Get-Command julia -ErrorAction SilentlyContinue)) { throw "Julia is required for VOLTERRA native acceptance" }
foreach ($jproj in @("optimization","simulation","routing","graph")) { Push-Location $jproj; julia --project=. -e 'using Pkg; Pkg.instantiate(); Pkg.test()'; if ($LASTEXITCODE -ne 0) { throw "VOLTERRA Julia tests failed: $jproj" }; Pop-Location }
& $python -m pip install -e ".\ml[dev]"
& $python -m pytest -q ml/tests
if ($LASTEXITCODE -ne 0) { throw "VOLTERRA ML tests failed" }
& $python -m pip install -e ".\ingestion[dev]"
& $python -c "import tesla, supplemental; print('VOLTERRA_INGESTION_IMPORT=PASS')"
if (-not (Get-Command go -ErrorAction SilentlyContinue)) { throw "Go is required for VOLTERRA backend acceptance" }
Push-Location backend; go test ./...; if ($LASTEXITCODE -ne 0) { throw "VOLTERRA Go tests failed" }; Pop-Location
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node/npm is required for VOLTERRA frontend acceptance" }
Push-Location frontend; npm ci; if ($LASTEXITCODE -ne 0) { throw "VOLTERRA npm ci failed" }; npm run build; if ($LASTEXITCODE -ne 0) { throw "VOLTERRA frontend build failed" }; Pop-Location
Write-Host "VOLTERRA_ENTERPRISE_ACCEPTANCE=PASS"
