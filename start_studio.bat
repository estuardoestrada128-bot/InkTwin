@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m inktwin studio --port 8765
    exit /b 0
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 -m inktwin studio --port 8765
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m inktwin studio --port 8765
    if %errorlevel% neq 0 pause
    exit /b %errorlevel%
)

echo [Error] Python 3.10+ not found on PATH.
pause
exit /b 1
