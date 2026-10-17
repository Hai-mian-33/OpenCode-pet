@echo off
REM OpenCode Pet - remove the pet, its startup entry and its state.

setlocal
cd /d "%~dp0.."
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Python 3.9 or newer is required and was not found on PATH.
    pause
    exit /b 1
)
python install.py uninstall %*
echo.
pause
