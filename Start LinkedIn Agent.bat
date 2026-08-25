@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   LinkedIn Conversation Assistant
echo ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    pause
    exit /b 1
)

python linkedin_gui.py
if errorlevel 1 (
    echo.
    echo The LinkedIn assistant exited with an error.
    pause
)
