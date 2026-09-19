# Restarts the dashboard when it is not serving.
#
# The census sampler only runs while the service runs, and every rate the
# opportunity model computes is a derivative that needs two observations of the
# same game. A service that is down is not just an unreachable page: it is a
# hole in the measurements that cannot be backfilled, because nobody can go and
# ask Roblox what the player counts were two hours ago.
#
# The ledger already carries two such holes, of 7.7 and 2.3 hours. The second
# one was the service exiting because it bound its address before Tailscale had
# brought the interface up. Task Scheduler is configured to restart the task on
# failure, but Windows applies that when a task fails to *launch*; a process
# that launches and then exits non-zero is left dead, which is what happened.
#
# So this checks, in order, because "the process exists" and "the service is
# serving" are different claims:
#   1. the dashboard answers on its address, whichever launcher started it --
#      if so, nothing else matters and nothing is logged;
#   2. the scheduled task is running at all;
#   3. the process it started is listening on a socket.
#
# A copy launched while another is serving is harmless now -- it finds the
# ledger held and exits before reading anything (app/main.py lifespan) -- but
# it is still not an outage, and it is not written down as one.
#
# Nothing is logged on the happy path. Every line in the log is an outage.

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TaskName = 'Roblox Venture Agents'
$LogPath = Join-Path $ProjectRoot 'data\watchdog.log'
$MaxLogBytes = 1MB

function Write-Outage([string]$Message) {
    $stamp = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
    if ((Test-Path -LiteralPath $LogPath) -and (Get-Item -LiteralPath $LogPath).Length -gt $MaxLogBytes) {
        Move-Item -LiteralPath $LogPath -Destination "$LogPath.1" -Force
    }
    Add-Content -LiteralPath $LogPath -Value "$stamp  $Message" -Encoding utf8
}

# First: is the dashboard answering, whoever started it? start-agents.bat runs
# the same app in a window of its own, and while that copy held the port this
# script saw only an idle task and launched a second copy every five minutes --
# 21 in one afternoon, each logged here as an outage that was not one. Any HTTP
# answer counts, 401 included, exactly as in start-agents.bat, and the address
# comes from the app's own settings the same way. If the address cannot be
# read, the checks below still run: they are what this script did before.
$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$preflight = Join-Path $ProjectRoot 'scripts\agents_preflight.py'
# Continue, not Stop, around native commands: Windows PowerShell turns any line
# a native program writes to stderr into an error, and under Stop a harmless
# warning from Python would abandon the probe.
$ErrorActionPreference = 'Continue'
try {
    $config = & $python $preflight config 2>$null
    $url = (@($config) -match '^DASH_URL=' | Select-Object -First 1) -replace '^DASH_URL=', ''
    if ($url) {
        & curl.exe -s -m 5 -o NUL $url 2>$null
        if ($LASTEXITCODE -eq 0) { exit 0 }
    }
} catch {
    # Fall through to the task checks.
} finally {
    $ErrorActionPreference = 'Stop'
}

try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
} catch {
    Write-Outage "The '$TaskName' task is not registered. Run scripts\install_startup.ps1."
    exit 1
}

$info = Get-ScheduledTaskInfo -TaskName $TaskName

if ($task.State -ne 'Running') {
    Write-Outage ("Service was not running (task state $($task.State), last result " +
                  "$($info.LastTaskResult), last run $($info.LastRunTime)). Starting it.")
    Start-ScheduledTask -TaskName $TaskName
    exit 0
}

# The task is running. That only means a process exists, so check that the
# process is actually listening: a service that started and then failed its
# bind would otherwise look healthy here forever.
#
# Identified by command line, not by executable path. A venv on Windows runs
# the base interpreter -- the service's own process reports
# `...\Python312\pythonw.exe`, not the venv copy the task was pointed at -- and
# a path match also catches the test fixture, which runs the same interpreter
# out of the same directory and is not this service.
$processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue `
        -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*app.service*' })

if (-not $processes) {
    Write-Outage 'The task reports Running but no app.service process exists. Restarting.'
    Stop-ScheduledTask -TaskName $TaskName
    Start-ScheduledTask -TaskName $TaskName
    exit 0
}

$owned = @($processes | ForEach-Object { [int]$_.ProcessId })
$listening = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $owned -contains [int]$_.OwningProcess })

if (-not $listening) {
    # It may still be inside the startup wait for a late interface, which is a
    # normal state for up to three minutes after a boot. Only an older process
    # than that is genuinely stuck.
    $started = ($processes | Sort-Object CreationDate | Select-Object -First 1).CreationDate
    if ($started -and ((Get-Date) - $started).TotalSeconds -lt 240) { exit 0 }
    Write-Outage 'The service process is alive but listening on nothing. Restarting.'
    Stop-ScheduledTask -TaskName $TaskName
    Start-ScheduledTask -TaskName $TaskName
}
