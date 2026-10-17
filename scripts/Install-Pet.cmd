@echo off
REM OpenCode Pet - one double click installs the pet and starts it.
REM
REM Copies the whole folder into %USERPROFILE%\.config\opencode\pet, writes one
REM hidden launcher into the Windows Startup folder, then starts the pet.
REM Run Install-Pet.cmd again to reinstall after moving this folder.

setlocal
cd /d "%~dp0.."
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Python 3.9 or newer is required and was not found on PATH.
    pause
    exit /b 1
)
python install.py install %*
echo.
pause
