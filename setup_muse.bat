@echo off
setlocal
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -B "integrations\muse\setup_muse.py"
) else (
    python -B "integrations\muse\setup_muse.py"
)
exit /b %errorlevel%
