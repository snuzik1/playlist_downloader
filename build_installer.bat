@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

echo Building PLDL.exe ...
call "%~dp0build_exe.bat" --quiet
if errorlevel 1 goto :fail

if exist "uninstaller\Program.cs" (
    call "%~dp0build_uninstaller.bat" --quiet
    if errorlevel 1 goto :fail
)

set "APPVER=0.1.0"
set "VERFILE=%~dp0build_version.tmp"
".venv\Scripts\python.exe" -c "import pathlib, sys, pldl; pathlib.Path(sys.argv[1]).write_text(pldl.__version__, encoding='utf-8')" "%VERFILE%"
if exist "%VERFILE%" (
    set /p APPVER=<"%VERFILE%"
    del "%VERFILE%" >nul 2>&1
)

set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    for /f "delims=" %%i in ('where iscc 2^>nul') do set "ISCC=%%i"
)
if not exist "%ISCC%" (
    echo.
    echo   Inno Setup 6 was not found.
    echo   Install it with: winget install JRSoftware.InnoSetup
    echo.
    goto :fail
)

echo Building installer %APPVER% ...
"%ISCC%" /Qp /DMyAppVersion=%APPVER% installer.iss
if errorlevel 1 goto :fail

copy /y "dist\PLDL-Setup-%APPVER%.exe" "PLDL-Setup.exe" >nul
if errorlevel 1 goto :fail

echo.
echo   Installer: %~dp0PLDL-Setup.exe
echo   Versioned copy: %~dp0dist\PLDL-Setup-%APPVER%.exe
echo.
if /i not "%~1"=="--quiet" pause
exit /b 0

:fail
echo.
echo   Installer build failed. Check the messages above.
echo.
if /i not "%~1"=="--quiet" pause
exit /b 1
