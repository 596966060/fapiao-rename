@echo off
cls

echo.
echo ========== Invoice Rename Tool ==========
echo.

REM Change to script directory
cd /d "%~dp0"
echo Current directory: %cd%
echo.

REM Check if files exist
if not exist "app.py" (
    echo Error: app.py not found
    echo Current path: %cd%
    echo.
    pause
    exit /b 1
)

if not exist "requirements.txt" (
    echo Error: requirements.txt not found
    pause
    exit /b 1
)

if not exist "templates" (
    echo Error: templates folder not found
    pause
    exit /b 1
)

echo Files OK
echo.
echo Checking Python...
echo.

python --version
if errorlevel 1 (
    echo.
    echo Error: Python not found
    echo Please install Python 3.7+
    echo https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo.
echo Checking dependencies...
echo.

pip list | find "Flask" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies, please wait...
    echo.
    pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Error installing dependencies
        pause
        exit /b 1
    )
)

echo.
echo Starting Flask server...
echo.
echo Access: http://127.0.0.1:5000
echo.
echo Opening browser in 2 seconds...
echo.

timeout /t 2 /nobreak

start http://127.0.0.1:5000

python app.py

pause
exit /b 0
