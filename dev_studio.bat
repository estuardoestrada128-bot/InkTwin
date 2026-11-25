@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

where python >nul 2>nul
if %errorlevel%==0 goto run_python

where py >nul 2>nul
if %errorlevel%==0 goto run_py

echo [Error] Python 3.10+ not found on PATH.
pause
exit /b 1

:run_python
echo Starting InkTwin Studio in live-reload dev mode. Press Ctrl+C to stop.
python -m inktwin studio --port 8765 --reload
if %errorlevel% neq 0 pause
exit /b %errorlevel%

:run_py
echo Starting InkTwin Studio in live-reload dev mode. Press Ctrl+C to stop.
py -3 -m inktwin studio --port 8765 --reload
if %errorlevel% neq 0 pause
exit /b %errorlevel%
