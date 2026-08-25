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

if not exist "connections.csv" (
    echo ERROR: connections.csv was not found beside the scripts.
    echo Put your LinkedIn connections export here and run this again.
    pause
    exit /b 1
)

echo Importing connections.csv...
python import_connections.py
if errorlevel 1 (
    echo.
    echo ERROR: Failed to import connections.csv.
    pause
    exit /b 1
)

echo.
echo Starting LinkedIn assistant...
python linkedin_gui.py
if errorlevel 1 (
    echo.
    echo The LinkedIn assistant exited with an error.
    pause
)
