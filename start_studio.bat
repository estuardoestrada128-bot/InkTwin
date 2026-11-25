@echo off
chcp 65001 >nul
setlocal
title InkTwin Studio
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

echo ==================================================
echo   InkTwin Studio - 本地离线手写工作台
echo   浏览器打开后请保持此窗口运行（可最小化），关闭即停止服务
echo ==================================================
echo.

where python >nul 2>nul
if %errorlevel%==0 (
    python -m inktwin studio --port 8765
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        py -3 -m inktwin studio --port 8765
    ) else (
        echo [错误] 未检测到 Python，请先安装 Python 3.10+ 并勾选 Add Python to PATH。
        pause
        exit /b 1
    )
)

if %errorlevel% neq 0 (
    echo.
    echo [提示] InkTwin Studio 启动失败，请检查上方报错信息或运行 pip install -e .
    pause
    exit /b %errorlevel%
)

