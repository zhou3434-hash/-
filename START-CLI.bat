@echo off
REM ============================================================
REM  Travel Assistant - Command Line Interface
REM  This file is intentionally ASCII-only.
REM  Reason: the system code page here is 936 (GBK). A UTF-8 file
REM  with Chinese text gets mis-decoded by cmd.exe, and some GBK
REM  double-byte pairs swallow the newline, merging lines together
REM  and breaking the script. ASCII avoids the problem entirely.
REM ============================================================
title Travel Assistant - CLI
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
chcp 65001 >nul 2>&1

set "PY=%~dp0.venv\Scripts\python.exe"

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
echo   Travel Assistant - Command Line Interface
echo   Type /help for commands, /exit to quit.
echo.

"%PY%" -m travel_assistant.cli %*
set "CODE=%ERRORLEVEL%"

if not "%CODE%"=="0" (
    echo.
    echo [INFO] Program exited with code %CODE%
    pause
)
endlocal
