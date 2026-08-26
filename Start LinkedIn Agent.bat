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

python -c "import playwright" >nul 2>nul
if errorlevel 1 (
    echo Installing required Python package: playwright...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install required packages.
        pause
        exit /b 1
    )
)

if not exist "connections.csv" (
    echo WARNING: connections.csv was not found.
    echo You can still use ^"Add prospect by URL^" for individual prospects.
    echo.
) else (
    echo Importing connections.csv...
    python import_connections.py
    if errorlevel 1 (
        echo.
        echo ERROR: Failed to import connections.csv.
        pause
        exit /b 1
    )
)

echo.
echo Starting LinkedIn assistant...
python linkedin_gui.py
if errorlevel 1 (
    echo.
    echo The LinkedIn assistant exited with an error.
    pause
)
