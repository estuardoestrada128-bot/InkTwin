@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

where python >nul 2>nul
if %errorlevel%==0 (
    echo Starting InkTwin Studio in live-reload dev mode (Ctrl+C to stop)...
    python -m inktwin studio --port 8765 --reload
    if %errorlevel% neq 0 pause
    exit /b %errorlevel%
)

where py >nul 2>nul
if %errorlevel%==0 (
    echo Starting InkTwin Studio in live-reload dev mode (Ctrl+C to stop)...
    py -3 -m inktwin studio --port 8765 --reload
    if %errorlevel% neq 0 pause
    exit /b %errorlevel%
)

echo [Error] Python 3.10+ not found on PATH.
pause
exit /b 1

