@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
if errorlevel 1 (
    echo Setup failed. Read the error above and try again.
    pause
    exit /b 1
)
echo Ready. Open start.bat to launch the studio.
pause
