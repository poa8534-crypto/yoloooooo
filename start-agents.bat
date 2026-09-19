@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Venture Agents - start everything
pushd "%~dp0"

REM  Starts everything the agents need, and says what is missing rather than
REM  failing halfway through.
REM
REM      start-agents.bat                              the project in .env
REM      start-agents.bat C:\RobloxGames\fisherman     that one instead
REM
REM  Each server gets its own minimised window, named, so you can close one
REM  without hunting through Task Manager. Anything already running is left
REM  alone, so this is safe to double-click twice.
REM
REM  `agy` and the Rokit tools are not servers -- they run per call -- so they
REM  are CHECKED rather than started. Discovering a missing formatter eight
REM  minutes into a build is the failure that check exists to prevent.

set "PY=.venv\Scripts\python.exe"
set "ROJO=%USERPROFILE%\.rokit\bin\rojo.exe"
set "PREFLIGHT=scripts\agents_preflight.py"
set "BRIDGE_URL=http://127.0.0.1:34873/bridge/hello"
set "ROJO_URL=http://127.0.0.1:34872/api/rojo"
set "CFG=%TEMP%\venture-agents-config.txt"

echo.
echo   Venture Agents
echo   --------------
echo.

if not exist "%PY%" (
    echo   [X] No virtual environment at %PY%
    echo       Create it with:  uv sync --extra dev --extra embeddings
    goto :fail
)
echo   [ok] Python environment

REM --------------------------------------------------------------- project --
"%PY%" "%PREFLIGHT%" config %~1 > "%CFG%"
if errorlevel 1 (
    echo   [X] Could not read the configuration. Is .env in place?
    goto :fail
)
for /f "usebackq tokens=1,* delims==" %%A in ("%CFG%") do set "%%A=%%B"
del "%CFG%" >nul 2>&1

if not exist "!GAME_DIR!\.git" (
    echo   [X] Not a git repository: !GAME_DIR!
    echo       Pass one:  start-agents.bat C:\RobloxGames\fisherman
    goto :fail
)
echo   [ok] Game project: !GAME_DIR!

REM ------------------------------------------------------------- toolchain --
echo   [..] Checking the toolchain
"%PY%" "%PREFLIGHT%" tools %~1
if errorlevel 1 (
    echo.
    echo   [X] Something a build needs is missing. Fix the lines marked X above.
    echo       agy:  install the Antigravity CLI, then open a NEW terminal --
    echo             a running process never sees a PATH change.
    echo       rojo / selene / stylua / luau-lsp:
    echo             cd !GAME_DIR!  then  rokit install
    goto :fail
)

REM ---------------------------------------------------------------- bridge --
curl.exe -s -m 3 -o nul "%BRIDGE_URL%"
if not errorlevel 1 (
    echo   [ok] Bridge already running on 34873
) else (
    echo   [..] Starting the bridge
    start "Venture bridge" /min "%PY%" -m app.bridge.run
    call :wait "%BRIDGE_URL%" "bridge"
    if errorlevel 1 goto :fail
)

REM ------------------------------------------------------------------ rojo --
curl.exe -s -m 3 -o nul "%ROJO_URL%"
if not errorlevel 1 (
    echo   [ok] Rojo already serving on 34872
) else (
    if not exist "%ROJO%" (
        echo   [X] rojo.exe not found at %ROJO%
        echo       cd !GAME_DIR!  then  rokit install
        goto :fail
    )
    echo   [..] Starting rojo serve
    start "Rojo serve" /min cmd /c "cd /d "!GAME_DIR!" && "%ROJO%" serve default.project.json"
    call :wait "%ROJO_URL%" "rojo"
    if errorlevel 1 goto :fail
)

REM ------------------------------------------------------------- dashboard --
REM  It binds the address in .env and answers 401 without a token, so ANY
REM  answer means it is up. The address is never 0.0.0.0: that setting is a
REM  Tailscale address here, and binding every interface instead would put the
REM  ledger and the override controls on whatever network this machine joins
REM  next.
curl.exe -s -m 3 -o nul "!DASH_URL!"
if not errorlevel 1 (
    echo   [ok] Dashboard already running at !DASH_URL!
) else (
    echo   [..] Starting the dashboard
    start "Venture dashboard" /min "%PY%" -m uvicorn app.main:app --host !DASH_HOST! --port !DASH_PORT!
    call :wait "!DASH_URL!" "dashboard"
    if errorlevel 1 goto :fail
)

echo.
echo   Everything is up.
echo.
echo     bridge      http://127.0.0.1:34873    the Studio plugin talks to this
echo     rojo        http://127.0.0.1:34872    serving !GAME_DIR!
echo     dashboard   http://!DASH_HOST!:!DASH_PORT!
echo.
echo   In Studio: Plugins - Venture Engineer. The pairing token is kept in
echo   data\bridge_token.txt and survives a restart, so it reconnects on its
echo   own. If it ever asks, that file has the token.
echo.
echo   Close the three minimised windows to stop everything.
echo.
popd
endlocal
pause
exit /b 0

REM ---------------------------------------------------------------------------
:wait
REM  %1 url, %2 name. Waits up to 30 seconds for it to answer.
set /a TRIES=0
:waitloop
curl.exe -s -m 2 -o nul %1
if not errorlevel 1 (
    echo   [ok] Started %~2
    exit /b 0
)
set /a TRIES+=1
if !TRIES! GEQ 30 (
    echo   [X] %~2 did not answer on %~1 within 30 seconds.
    echo       Its window is minimised -- open it to see the reason.
    exit /b 1
)
ping -n 2 127.0.0.1 >nul
goto :waitloop

:fail
echo.
echo   Startup stopped. Nothing further was started.
echo.
popd
endlocal
pause
exit /b 1
