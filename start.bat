@echo off
title Marstek Dashboard

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

if not exist "%ROOT%\backend\venv" (
    echo [ERROR] Not installed yet. Please run install.bat first.
    pause
    exit /b 1
)

if not exist "%ROOT%\frontend\dist" (
    echo [ERROR] Frontend not built yet. Please run install.bat first.
    pause
    exit /b 1
)

echo Starting Marstek Dashboard...
echo Dashboard will open at http://localhost:5000
echo Press Ctrl+C in this window to stop the server.
echo.

:: Open browser after a short delay
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:5000"

cd /d "%ROOT%\backend"
call venv\Scripts\activate.bat
python app.py

pause
