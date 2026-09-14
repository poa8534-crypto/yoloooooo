$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
Set-Location -LiteralPath $ProjectRoot

$process = Start-Process -FilePath $Python -ArgumentList '-m','app.main' -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 2
Start-Process 'http://127.0.0.1:8742'
Write-Output "Dashboard started as process $($process.Id)."

