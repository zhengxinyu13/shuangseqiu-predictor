@echo off
rem Launch the SSQ picker UI.
rem Content is deliberately ASCII-only: %~dp0 expands to the project folder at
rem runtime, so the non-ASCII characters in the project path never appear in
rem this file (a literal non-ASCII path in a .bat can be mangled by the console
rem code page).
set "PICKER_PYTHON=%USERPROFILE%\.workbuddy\binaries\python\envs\ssq-picker\Scripts\python.exe"
if not exist "%PICKER_PYTHON%" (
    echo [ERROR] Picker venv not found:
    echo         %PICKER_PYTHON%
    echo Create it with:  C:\Python314\python.exe -m venv "%USERPROFILE%\.workbuddy\binaries\python\envs\ssq-picker"
    pause
    exit /b 1
)
"%PICKER_PYTHON%" "%~dp0picker.py" %*
if errorlevel 1 pause
