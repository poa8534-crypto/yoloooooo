# Start the local SearxNG instance used for discovery.
#
# SearxNG is a metasearch front end: it queries public search engines and
# returns the results as JSON. Running it locally removes the third-party
# search API key and its daily allowance from the research pipeline. It is
# bound to loopback and is not reachable from the network.
#
#   powershell -ExecutionPolicy Bypass -File scripts\start_searxng.ps1
#
# Stop it with Ctrl+C, or close the window it runs in.

$ErrorActionPreference = 'Stop'

$root = 'C:\Users\tcgxu\searxng'
$settings = Join-Path $root 'settings-local.yml'
$python = Join-Path $root '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Error "SearxNG is not installed at $root. See docs/LOCAL_SEARCH.md."
}

# A generated secret, written once. The checked-in placeholder must never be
# the key an instance actually runs with.
$content = Get-Content $settings -Raw
if ($content -match 'change-me-local-only') {
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $secret = -join ($bytes | ForEach-Object { $_.ToString('x2') })
    ($content -replace 'change-me-local-only', $secret) | Set-Content $settings -Encoding utf8
    Write-Host '  [ok] generated a local secret key'
}

$env:SEARXNG_SETTINGS_PATH = $settings
$env:PYTHONPATH = $root

Write-Host "  [..] starting SearxNG on http://127.0.0.1:8888"
& $python -m searx.webapp
