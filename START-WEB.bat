@echo off
REM ============================================================
REM  Travel Assistant - Web Interface
REM  This file is intentionally ASCII-only.
REM  Reason: the system code page here is 936 (GBK). A UTF-8 file
REM  with Chinese text gets mis-decoded by cmd.exe, and some GBK
REM  double-byte pairs swallow the newline, merging lines together
REM  and breaking the script. ASCII avoids the problem entirely.
REM ============================================================
title Travel Assistant - Web
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
chcp 65001 >nul 2>&1

set "PY=%~dp0.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:8000"

if not exist "%PY%" (
    echo.
    echo [ERROR] Virtual environment not found: .venv
    echo         Run these first:
    echo             uv venv --python 3.14
    echo             uv sync --extra dev
    echo.
    pause
    exit /b 1
)

echo.
echo   Travel Assistant - Web Interface
echo   ==========================================
echo   Address: %URL%
echo.
echo   KEEP THIS WINDOW OPEN while using the web page.
echo   Closing it stops the server, and the page will then
echo   show "Failed to fetch".
echo   ==========================================
echo.

REM Start the server in its own window, then poll the port and only
REM open the browser once it is actually listening (otherwise the
REM browser hits a connection error).
start "travel-assistant-server" cmd /k ""%PY%" -m travel_assistant.server"

echo   Waiting for the server to come up...
set /a TRIES=0
:waitloop
set /a TRIES+=1
powershell -NoProfile -Command "try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',8000);$c.Close();exit 0}catch{exit 1}" >nul 2>&1
if not errorlevel 1 goto ready
if %TRIES% GEQ 40 goto failed
timeout /t 1 /nobreak >nul
goto waitloop

:ready
echo   OK - server is up. Opening the browser.
start "" "%URL%"
goto end

:failed
echo.
echo   [WARN] Server did not become ready within 40 seconds.
echo          Check the other window (travel-assistant-server) for errors.
echo          You can also open the address manually: %URL%
echo.

:end
echo.
echo   Server window: travel-assistant-server
echo   Run DIAG.bat if something goes wrong.
echo.
timeout /t 8 /nobreak >nul
endlocal
