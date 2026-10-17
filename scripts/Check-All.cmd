@echo off
REM OpenCode Pet - run the unit tests and the self-test renderer.

setlocal
cd /d "%~dp0.."
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Python 3.9 or newer is required and was not found on PATH.
    exit /b 1
)

echo == unit tests ==============================================
python tests\run_tests.py
if %ERRORLEVEL% NEQ 0 goto failed

echo.
echo == selftest renderer =======================================
python opencode_pet.py --selftest
if %ERRORLEVEL% NEQ 0 goto failed

echo.
echo == install status =========================================
python install.py status
exit /b 0

:failed
echo.
echo FAILED - see the output above.
exit /b 1
