@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo   PLDL setup
echo.

set "PY="
where python >nul 2>&1
if not errorlevel 1 set "PY=python"
if not defined PY (
    where py >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
    echo Python was not found.
    echo Install Python 3.10 or newer from https://www.python.org/downloads/windows/
    echo During installation enable "Add Python to PATH".
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment .venv ...
    %PY% -m venv .venv
    if errorlevel 1 goto :fail
)

echo Updating pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
echo Installing dependencies ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo   WARNING: FFmpeg was not found in PATH.
    echo   MP3/M4A conversion and cover art require FFmpeg.
    echo   Install it with: winget install Gyan.FFmpeg
    echo   Then restart setup.bat or set the ffmpeg.exe path in the app settings.
)

echo.
echo   Done. Run run.bat to start the app.
echo.
if /i not "%~1"=="--quiet" pause
exit /b 0

:fail
echo.
echo   Setup failed. Check the messages above.
echo.
if /i not "%~1"=="--quiet" pause
exit /b 1
