@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

:: Set Window Title
title Douyin2TikTok AI Studio Launcher

:: 1. Check Python Virtual Environment
if exist "venv\Scripts\pythonw.exe" (
    set "PYTHONW_BIN=venv\Scripts\pythonw.exe"
    set "PYTHON_BIN=venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PYTHONW_BIN=venv\Scripts\python.exe"
    set "PYTHON_BIN=venv\Scripts\python.exe"
) else (
    echo [ERROR] Virtual environment 'venv' not found in: %cd%\venv
    mshta "javascript:var sh=new ActiveXObject('WScript.Shell'); sh.Popup('Khong tim thay thu muc virtual environment tai:\n%cd%\\venv\n\nVui long cai dat moi truong Python truoc.', 0, 'Douyin2TikTok AI Studio - Loi', 16); close();"
    pause
    exit /b 1
)

:: 2. Check FFmpeg in PATH
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] FFmpeg is not found in system PATH.
    mshta "javascript:var sh=new ActiveXObject('WScript.Shell'); sh.Popup('Canh bao: FFmpeg khong duoc tim thay trong PATH.\nMot so chuc nang xu ly video co the bi anh huong.', 5, 'Douyin2TikTok AI Studio', 48); close();"
)

:: 3. Launch Desktop Studio silently with pythonw.exe (No black terminal window)
start "" "%PYTHONW_BIN%" desktop_app.py %*

:: Exit immediately to free terminal
exit /b 0
