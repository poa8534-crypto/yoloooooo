# The single definition of "this code is acceptable".
#
# Everything that writes Luau here -- the Engineer Agent's retry loop, Hermes,
# a person at a terminal -- runs this one script. One definition means a change
# to the standard is a change to this file, visible in review, rather than four
# slightly different command lines drifting apart in four places.
#
# Every check runs even after an earlier one fails, because an author fixing
# three problems in one pass beats three round trips.
#
# Exit code is 0 only if every check passed.

[CmdletBinding()]
param(
    # The guard lives in the agents repo, not this one. This repo is what the
    # agents write, and a gate stored beside the thing it polices can be edited
    # by whatever it was meant to stop.
    [string]$AgentsRepo = $(if ($env:VENTURE_AGENTS_REPO) { $env:VENTURE_AGENTS_REPO }
                            else { "C:\Users\tcgxu\OneDrive\Desktop\roblox-venture-agents" }),

    # Run the Roblox toolchain alone. Named so that using it is a deliberate
    # statement: without the guard, 'game' and 'script' go unchecked.
    [switch]$WithoutGuard
)

$ErrorActionPreference = "Continue"
$GameRepo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $GameRepo
$env:PATH = "$env:USERPROFILE\.rokit\bin;$env:PATH"

$failures = New-Object System.Collections.ArrayList

function Invoke-Check {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ""
    Write-Host "-- $Name" -ForegroundColor Cyan
    $output = & $Body 2>&1
    $ok = ($LASTEXITCODE -eq 0)
    if ($output) { $output | ForEach-Object { "   $_" } }
    if ($ok) {
        Write-Host "   PASS" -ForegroundColor Green
    } else {
        Write-Host "   FAIL (exit $LASTEXITCODE)" -ForegroundColor Red
        [void]$failures.Add($Name)
    }
}

# The sourcemap is an input to the type check rather than a check of its own,
# so if it cannot be built the remaining results would be meaningless.
Write-Host "-- sourcemap" -ForegroundColor Cyan
rojo sourcemap default.project.json -o sourcemap.json | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "   FAIL: could not build the sourcemap; the type check needs it" -ForegroundColor Red
    exit 1
}
Write-Host "   PASS" -ForegroundColor Green

Invoke-Check "selene (lint)" { selene src }

Invoke-Check "stylua (format)" { stylua --check src }

# The definitions file is an input, not an option.
#
# luau-lsp prints `[ERROR] Failed to read definitions file ... Extended types
# will not be provided` when it is absent, and then exits 0. This gate read
# that as PASS. Measured in a fresh git worktree, where the file is gitignored
# and therefore absent: a clean tree passed with no Roblox types loaded at all,
# and `Services.Players:MadeUpMethod()` drew no diagnostic whatsoever -- the
# whole reason the Services module exists. The run happened to exit 1 only
# because `Services.luau` itself reported `Unknown type 'Players'`, so the exit
# code was right by accident and would stop being right the moment that second
# failure went away.
#
# Every engineer run works in a worktree, so this was not an edge case.
if (-not (Test-Path -LiteralPath (Join-Path $GameRepo "globalTypes.None.d.luau"))) {
    Write-Host ""
    Write-Host "-- luau-lsp (types)" -ForegroundColor Cyan
    Write-Host "   FAIL: globalTypes.None.d.luau is missing." -ForegroundColor Red
    Write-Host "   luau-lsp degrades to no Roblox types and still exits 0, so this" -ForegroundColor Red
    Write-Host "   refuses rather than reporting a pass nothing checked." -ForegroundColor Red
    Write-Host "   It is generated and gitignored; a fresh clone or worktree needs:" -ForegroundColor Red
    Write-Host "     curl -L -o globalTypes.None.d.luau https://raw.githubusercontent.com/JohnnyMorganz/luau-lsp/main/scripts/globalTypes.None.d.luau" -ForegroundColor Red
    [void]$failures.Add("luau-lsp (types)")
} else {
    # Both flags are load-bearing. The plain globalTypes.d.luau is the
    # RobloxScriptSecurity dump, under which Instance.new("Part"):GetDebugId()
    # type-checks clean and then fails at run time.
    Invoke-Check "luau-lsp (types)" {
        luau-lsp analyze --platform=roblox --definitions=globalTypes.None.d.luau --sourcemap=sourcemap.json src
    }
}

# What luau-lsp cannot see: 'game' and 'script' are sourcemap nodes, so nothing
# reached through them is type-checked. Both were confirmed accepted by the
# type checker on this toolchain, and are refused here instead.
if (-not $WithoutGuard) {
    $python = Join-Path $AgentsRepo ".venv\Scripts\python.exe"
    $runner = Join-Path $AgentsRepo "scripts\guard_check.py"
    if ((Test-Path -LiteralPath $python) -and (Test-Path -LiteralPath $runner)) {
        Invoke-Check "guard (game / script)" {
            & $python $runner (Join-Path $GameRepo "src")
        }
    } else {
        Write-Host ""
        Write-Host "-- guard (game / script)" -ForegroundColor Cyan
        Write-Host "   FAIL: guard not runnable from $AgentsRepo" -ForegroundColor Red
        Write-Host "   Set VENTURE_AGENTS_REPO to the agents checkout, or pass" -ForegroundColor Red
        Write-Host "   -WithoutGuard and accept that game/script go unchecked." -ForegroundColor Red
        [void]$failures.Add("guard (game / script)")
    }
}

# The five checks above read the code; not one of them runs it. SalvageNode
# passed all five while reporting a node full immediately after it was
# harvested and empty once it had refilled. Every spec assertion is a line from
# that system's acceptance criteria, so this is the check that asks whether the
# code does what was asked rather than whether it compiles.
$runner = Join-Path $GameRepo "tests\runner.luau"
if (Test-Path -LiteralPath $runner) {
    Invoke-Check "behaviour (lune)" { lune run tests/runner.luau }
} else {
    Write-Host ""
    Write-Host "-- behaviour (lune)" -ForegroundColor Cyan
    Write-Host "   FAIL: tests/runner.luau is missing; the behaviour checks cannot run." -ForegroundColor Red
    Write-Host "   A missing check is worse than a failing one: it passes silently." -ForegroundColor Red
    [void]$failures.Add("behaviour (lune)")
}

Write-Host ""
if ($failures.Count -eq 0) {
    Write-Host "ALL CHECKS PASSED" -ForegroundColor Green
    exit 0
}
Write-Host "FAILED: $($failures -join ', ')" -ForegroundColor Red
exit 1
