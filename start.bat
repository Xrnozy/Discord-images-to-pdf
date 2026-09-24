@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo  Discord Screenshot PDF
echo ============================================
echo.

where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON=py
    goto :found_python
)
where python >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PYTHON=python
    goto :found_python
)

echo ERROR: Python is not installed or not on PATH.
echo Install Python 3.10+ from https://www.python.org/downloads/
echo Make sure to check "Add Python to PATH" during installation.
echo.
pause
exit /b 1

:found_python
echo Using: %PYTHON%
echo.

if not exist ".venv" (
    echo Creating virtual environment...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)

call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo Failed to activate virtual environment.
    pause
    exit /b 1
)

echo Installing dependencies...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo Failed to install requirements.
    pause
    exit /b 1
)

if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" >nul
        echo Created .env from .env.example — add your bot token there or in the website.
    )
)

set "BOT_APP=%~dp0..\gmeet-auto-ss\ScreenshotToDiscord\bin\Debug\net8.0-windows\ScreenshotToDiscord.exe"
if exist ".env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
        if /I "%%a"=="DISCORD_BOT_APP" set "BOT_APP=%%b"
    )
)

if exist "%BOT_APP%" (
    echo Starting Discord screenshot bot...
    start "ScreenshotToDiscord" "%BOT_APP%"
) else (
    echo.
    echo WARNING: Discord bot app not found:
    echo   %BOT_APP%
    echo.
    echo Build ScreenshotToDiscord in gmeet-auto-ss, or set DISCORD_BOT_APP in .env
    echo.
)

set OPEN_BROWSER=1
echo.
echo Starting PDF website at http://localhost:8000
echo Press Ctrl+C to stop.
echo.

python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
pause
