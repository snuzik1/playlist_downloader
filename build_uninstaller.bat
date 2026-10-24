@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if not exist "%CSC%" set "CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe"
if not exist "%CSC%" (
    echo.
    echo   C# compiler not found. Install .NET Framework 4.8 or build on Windows 10/11.
    echo.
    goto :fail
)

echo Building PLDL-Uninstall.exe ...
"%CSC%" /nologo /target:winexe /platform:anycpu /optimize+ /codepage:65001 /win32icon:assets\pldl.ico /out:PLDL-Uninstall.exe uninstaller\Program.cs
if errorlevel 1 goto :fail

echo.
echo   Uninstaller: %~dp0PLDL-Uninstall.exe
echo.
if /i not "%~1"=="--quiet" pause
exit /b 0

:fail
echo.
echo   Uninstaller build failed. Check the messages above.
echo.
if /i not "%~1"=="--quiet" pause
exit /b 1
