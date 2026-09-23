@echo off
rem ============================================================
rem  Build the SSQ picker as a standalone Windows exe.
rem
rem  Usage:
rem      build_exe.bat            folder build (recommended)
rem      build_exe.bat onefile    single-file build
rem
rem  Output:
rem      dist\ShuangseqiuPicker\ShuangseqiuPicker.exe   (folder)
rem      dist\ShuangseqiuPicker.exe                      (onefile)
rem
rem  Content is deliberately ASCII-only, same reason as run_picker.bat:
rem  a literal non-ASCII path inside a .bat can be mangled by the console
rem  code page. %~dp0 expands the real project path at runtime instead.
rem ============================================================
setlocal
cd /d "%~dp0"

set "VENV=%USERPROFILE%\.workbuddy\binaries\python\envs\ssq-build"
set "PY=%VENV%\Scripts\python.exe"
set "SSQ_ONEFILE="
if /i "%~1"=="onefile" set "SSQ_ONEFILE=1"

echo [1/3] Preparing build environment ...
if not exist "%PY%" (
    call :probe py -3.14
    if not defined BASE call :probe py -3
    if not defined BASE call :probe python
    if not defined BASE (
        echo.
        echo [ERROR] No Python with tkinter found.
        echo         Install official Python 3.13 / 3.14 and make sure
        echo         "tcl/tk and IDLE" is ticked in the installer.
        exit /b 1
    )
    echo         Creating venv with: %BASE%
    %BASE% -m venv "%VENV%"
    if errorlevel 1 exit /b 1
    "%PY%" -m pip install --disable-pip-version-check --quiet pyinstaller openpyxl httpx
    if errorlevel 1 exit /b 1
) else (
    echo         venv found: %VENV%
)

echo [2/3] Checking build dependencies ...
"%PY%" -c "import PyInstaller, openpyxl, httpx" >nul 2>&1
if errorlevel 1 (
    "%PY%" -m pip install --disable-pip-version-check --quiet pyinstaller openpyxl httpx
    if errorlevel 1 exit /b 1
)

echo [3/3] Building ^(SSQ_ONEFILE=%SSQ_ONEFILE%^) ...
"%PY%" -m PyInstaller --noconfirm --clean build_exe.spec
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed, see the log above.
    exit /b 1
)

echo.
echo Build finished.
if defined SSQ_ONEFILE (
    echo   dist\ShuangseqiuPicker.exe
) else (
    echo   dist\ShuangseqiuPicker\ShuangseqiuPicker.exe
)
echo.
echo On first run the app creates data\ next to the exe and seeds it with the
echo bundled history. Ship the exe (or the whole folder) as-is; recipients
echo need no Python installed.
exit /b 0

:probe
%~1 %~2 -c "import tkinter" >nul 2>&1
if not errorlevel 1 set "BASE=%~1 %~2"
goto :eof
