param([switch]$SkipNative)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if ($SkipNative -or $env:VOLTERRA_SKIP_NATIVE -eq "1") { Write-Host "VOLTERRA_NATIVE_ACCEPTANCE=SKIPPED_EXPLICITLY"; exit 0 }
function Require-Command([string]$Name) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw "Required VOLTERRA toolchain command not found: $Name" }
}
Require-Command "npm"
Require-Command "go"
Require-Command "julia"
if (-not (Test-Path ".venv\Scripts\python.exe")) { py -m venv .venv }
& .\.venv\Scripts\python.exe -m pip install -e ".\ml[dev]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m pytest -q .\ml\tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& .\.venv\Scripts\python.exe -m pip install -e ".\ingestion[dev]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (Test-Path ".\ingestion\tests") { & .\.venv\Scripts\python.exe -m pytest -q .\ingestion\tests; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } }
Push-Location .\backend
try { go test ./...; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } } finally { Pop-Location }
Push-Location .\frontend
try { npm ci; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; npm run build; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE } } finally { Pop-Location }
foreach ($module in @("graph","routing","optimization","simulation")) {
  $projectPath = Join-Path $root $module
  & julia "--project=$projectPath" -e "using Pkg; Pkg.resolve(); Pkg.instantiate(); Pkg.precompile()"
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
Write-Host "VOLTERRA_NATIVE_ACCEPTANCE=PASS"
