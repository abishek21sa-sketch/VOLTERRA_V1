$ErrorActionPreference = "Stop"
Write-Host "VOLTERRA Portfolio RC1 Acceptance"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
py -3 -m pip install scipy pytest
$env:PYTHONPATH = $root
py -3 -m pytest -q validation/tests
py -3 validation/run_portfolio_validation.py
if (-not (Get-Command julia -ErrorAction SilentlyContinue)) { throw "Julia is required for native VOLTERRA optimization acceptance." }
julia --project=optimization -e 'using Pkg; Pkg.instantiate(); Pkg.test()'
Write-Host "VOLTERRA_PORTFOLIO_RC1_ACCEPTANCE=PASS"
