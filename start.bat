@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Roblox Venture Agents
pushd "%~dp0"

REM The backend serves the built dashboard from frontend\dist at the same
REM origin, so one server is the whole application. Override the port with
REM  set VENTURE_PORT=8799 & start.bat
if "%VENTURE_PORT%"=="" set "VENTURE_PORT=8742"
set "PORT=%VENTURE_PORT%"
set "URL=http://127.0.0.1:%PORT%"
set "PY=.venv\Scripts\python.exe"

echo.
echo   Roblox Venture Agents
echo   ---------------------
echo.

REM ---------------------------------------------------------------- python --
if not exist "%PY%" (
    echo   [X] Virtual environment missing: %PY%
    echo.
    echo       Create it first:
    echo           uv sync --extra dev --extra embeddings
    goto :fail
)
echo   [ok] Python environment

REM -------------------------------------------------------------- dashboard --
if not exist "frontend\dist\index.html" (
    echo   [..] Building the dashboard ^(first run only, needs Node^)
    where npm >nul 2>&1
    if errorlevel 1 (
        echo   [X] npm was not found on PATH. Install Node.js, then run this again.
        goto :fail
    )
    if not exist "frontend\node_modules" (
        echo   [..] Installing dashboard packages
        pushd frontend
        call npm install --no-fund --no-audit
        set "RC=!errorlevel!"
        popd
        if not "!RC!"=="0" (
            echo   [X] npm install failed.
            goto :fail
        )
    )
    pushd frontend
    call npm run build
    set "RC=!errorlevel!"
    popd
    if not "!RC!"=="0" (
        echo   [X] Dashboard build failed.
        goto :fail
    )
)
echo   [ok] Dashboard built

REM ------------------------------------------------------- already running? --
curl.exe -s -m 3 -o nul "%URL%/api/health" 2>nul
if not errorlevel 1 (
    echo   [ok] Server already running on port %PORT%
    set "STARTED_HERE=0"
    goto :open
)

REM -------------------------------------------------------------- start it --
echo   [..] Starting the server on port %PORT%
start "Roblox Venture Agents server" /min "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%

set /a TRIES=0
:wait
curl.exe -s -m 2 -o nul "%URL%/api/health" 2>nul
if not errorlevel 1 goto :started
set /a TRIES+=1
if !TRIES! GEQ 40 goto :timeout
ping -n 2 127.0.0.1 >nul
goto :wait

:started
echo   [ok] Server started
set "STARTED_HERE=1"

:open
echo.
echo   Dashboard: %URL%
echo.
start "" "%URL%"
if "!STARTED_HERE!"=="1" (
    echo   The server runs in its own minimised window titled
    echo   "Roblox Venture Agents server". Close that window to stop it.
) else (
    echo   It was already running - most likely the "Roblox Venture Agents"
    echo   scheduled task, which starts at logon. To stop or restart it:
    echo       Stop-ScheduledTask  -TaskName ^'Roblox Venture Agents^'
    echo       Start-ScheduledTask -TaskName ^'Roblox Venture Agents^'
)
echo.
echo   This window can be closed.
ping -n 6 127.0.0.1 >nul
popd
endlocal
exit /b 0

:timeout
echo.
echo   [X] The server did not answer on %URL% within 40 seconds.
echo.
echo       Check data\service.log, or run this to see the error directly:
echo           .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
goto :fail

:fail
echo.
echo   Startup stopped. Nothing was changed.
echo.
popd
endlocal
pause
exit /b 1
