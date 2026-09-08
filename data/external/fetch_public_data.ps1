param([string]$ApiKey = "DEMO_KEY")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "https://developer.nlr.gov/api/alt-fuel-stations/v1.json?fuel_type=ELEC&state=IL&limit=200&api_key=$ApiKey"
Invoke-WebRequest -Uri $url -OutFile (Join-Path $root "afdc_il_ev_stations.json")
Write-Host "EXTERNAL_DATA_REFRESH=PASS"
