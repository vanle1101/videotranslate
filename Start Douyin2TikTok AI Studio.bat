@echo off
setlocal
cd /d "%~dp0"
title Douyin2TikTok AI Studio
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "venv\Scripts\pythonw.exe" (
    echo Python environment not found. Run setup.bat first.
    pause
    exit /b 1
)
"venv\Scripts\python.exe" -B check_runtime.py
if errorlevel 1 (
    echo Please run setup.bat to repair the installation.
    pause
    exit /b 1
)
start "" "venv\Scripts\pythonw.exe" -B desktop_app.py %*
exit /b 0
