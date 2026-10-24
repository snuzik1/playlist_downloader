@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    call "%~dp0setup.bat" --quiet
    if errorlevel 1 goto :fail
)

echo Installing build tools ...
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto :fail

echo Building PLDL.exe ...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean PLDL.spec
if errorlevel 1 goto :fail

copy /y "dist\PLDL.exe" "PLDL.exe" >nul
if errorlevel 1 goto :fail

echo.
echo   Built: %~dp0PLDL.exe
echo   A copy is also in %~dp0dist\PLDL.exe
echo   Move that one file anywhere and run it.
echo   settings.json and pldl.log will appear next to it.
echo.
if /i not "%~1"=="--quiet" pause
exit /b 0

:fail
echo.
echo   Build failed. Check the messages above.
echo.
if /i not "%~1"=="--quiet" pause
exit /b 1
