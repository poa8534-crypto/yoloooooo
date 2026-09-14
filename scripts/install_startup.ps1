$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Pythonw = Join-Path $ProjectRoot '.venv\Scripts\pythonw.exe'
$TaskName = 'Roblox Venture Agents'
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $Pythonw)) {
    throw "The project virtual environment is missing: $Pythonw"
}

$Action = New-ScheduledTaskAction `
    -Execute $Pythonw `
    -Argument '-m app.service' `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description 'Starts the local evidence-first Roblox research dashboard at logon.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Output "Registered and started '$TaskName' for $UserId."
