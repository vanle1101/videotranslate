@echo off
chcp 65001 >nul
title Douyin2TikTok AI Studio PRO
echo ========================================================
echo    🚀 Đang khởi động Douyin2TikTok AI Studio PRO...
echo ========================================================
echo.

set PYTHONIOENCODING=utf-8

if not exist "venv\Scripts\python.exe" (
    echo [!] Chua tim thay venv. Dang tao moi...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

echo [*] Server dang chay tai: http://127.0.0.1:8000
echo [*] Dang tu dong mo trinh duyet...
start http://127.0.0.1:8000

python main.py
pause
