@echo off
rem ============================================================
rem  Build the SSQ picker as a standalone Windows exe and
rem  assemble a release archive you can hand to someone else.
rem
rem  Usage:
rem      build_exe.bat            folder build (recommended)
rem      build_exe.bat onefile    single-file build
rem
rem  Output:
rem      the .zip under dist\ assembled by make_release.py
rem      (folder build -> release folder + zip; onefile build -> single exe + zip)
rem
rem  Content is deliberately ASCII-only, same reason as run_picker.bat:
rem  a literal non-ASCII path inside a .bat can be mangled by the console
rem  code page. %~dp0 expands the real project path at runtime instead.
rem  That is also why the Chinese output paths are printed by
rem  make_release.py (Python handles UTF-8 fine) instead of echoed here.
rem ============================================================
setlocal
cd /d "%~dp0"

set "VENV=%USERPROFILE%\.workbuddy\binaries\python\envs\ssq-build"
set "PY=%VENV%\Scripts\python.exe"
set "SSQ_ONEFILE="
if /i "%~1"=="onefile" set "SSQ_ONEFILE=1"

echo [1/4] Preparing build environment ...
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

echo [2/4] Checking build dependencies ...
"%PY%" -c "import PyInstaller, openpyxl, httpx" >nul 2>&1
if errorlevel 1 (
    "%PY%" -m pip install --disable-pip-version-check --quiet pyinstaller openpyxl httpx
    if errorlevel 1 exit /b 1
)

echo [3/4] Building ^(SSQ_ONEFILE=%SSQ_ONEFILE%^) ...
"%PY%" -m PyInstaller --noconfirm --clean build_exe.spec
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed, see the log above.
    exit /b 1
)

echo [4/4] Assembling the release archive ...
if defined SSQ_ONEFILE (
    "%PY%" make_release.py onefile
) else (
    "%PY%" make_release.py folder
)
if errorlevel 1 (
    echo.
    echo [ERROR] Release assembly failed. See the messages above.
    exit /b 1
)

echo.
echo Done. The .zip under dist\ is what you hand out.
echo Recipients need no Python, and the data file ships inside the app.
exit /b 0

:probe
%~1 %~2 -c "import tkinter" >nul 2>&1
if not errorlevel 1 set "BASE=%~1 %~2"
goto :eof
