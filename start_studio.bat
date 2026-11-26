@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "WORKDIR=%LOCALAPPDATA%\InkTwin\studio_work"
if not exist "%WORKDIR%" mkdir "%WORKDIR%"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m inktwin studio --port 8765 --workdir "%WORKDIR%"
    if %errorlevel%==0 exit /b 0
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 -m inktwin studio --port 8765 --workdir "%WORKDIR%"
    if %errorlevel%==0 exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m inktwin studio --port 8765 --workdir "%WORKDIR%"
    if %errorlevel% neq 0 (
        echo [Error] InkTwin Studio exited unexpectedly ^(code %errorlevel%^).
        pause
    )
    exit /b %errorlevel%
)

echo [Error] Python 3.10+ not found on PATH.
pause
exit /b 1
