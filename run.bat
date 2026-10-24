@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: preparing the environment...
    call "%~dp0setup.bat" --quiet
    if errorlevel 1 (
        echo.
        echo Could not prepare the environment. Run setup.bat and check the errors.
        pause
        exit /b 1
    )
)

echo Starting PLDL...
start "" ".venv\Scripts\pythonw.exe" desktop.py %*
if errorlevel 1 (
    echo.
    echo The app could not start. See pldl.log next to this file.
    pause
)
