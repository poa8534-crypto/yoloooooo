$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
Set-Location -LiteralPath $ProjectRoot

& $Python -m app.service
exit $LASTEXITCODE
