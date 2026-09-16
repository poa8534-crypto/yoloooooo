# Registers the watchdog: checks every five minutes that the dashboard is
# serving, and restarts it if it is not.
#
# Five minutes is chosen against the thing being protected. The census samples
# every thirty minutes, so this bounds a crash to at most one missed census
# rather than to however long it takes somebody to notice the page is down --
# which, last time, was two hours.

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Watchdog = Join-Path $PSScriptRoot 'watchdog.ps1'
$TaskName = 'Roblox Venture Agents Watchdog'
$UserId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if (-not (Test-Path -LiteralPath $Watchdog)) {
    throw "The watchdog script is missing: $Watchdog"
}

$Action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Watchdog`"" `
    -WorkingDirectory $ProjectRoot

# At logon, then every five minutes for as long as the session lasts. The
# service's own task is also AtLogOn, so the first check lands a few minutes
# after it has had its chance to start.
#
# The duration is left unset on purpose. `[TimeSpan]::MaxValue` serialises to
# `P99999999DT23H59M59S`, which the task scheduler rejects outright; an absent
# duration is what "repeat indefinitely" actually means here.
#
# Two triggers, not one. A logon trigger's repetition is armed by the logon,
# so installing the watchdog mid-session would leave it dormant until the next
# one -- which is exactly the window somebody installs it to cover. The Once
# trigger starts the five-minute cycle now; the logon trigger restarts it after
# every reboot.
$Repeating = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$AtLogon = New-ScheduledTaskTrigger -AtLogOn -User $UserId
$AtLogon.Repetition = $Repeating.Repetition
$Trigger = @($Repeating, $AtLogon)

$Principal = New-ScheduledTaskPrincipal -UserId $UserId -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description 'Restarts the Roblox research dashboard whenever it stops serving, so census sampling does not leave a hole that cannot be backfilled.' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Output "Registered '$TaskName'. It checks every 5 minutes; outages are logged to data\watchdog.log."
