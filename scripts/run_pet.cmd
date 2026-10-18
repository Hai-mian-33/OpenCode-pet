@echo off
REM OpenCode Pet - start the pet without a console window.
REM Double-click this file, or run it from a terminal while developing.
REM
REM This is also the way back after choosing Exit in the tray menu: it starts the
REM pet by hand, which clears the "stay closed" request the way the background
REM restart must not.

setlocal
REM The entry point lives one level up, next to this scripts folder.  Using
REM %~dp0 alone pointed at scripts\opencode_pet.py, which does not exist, so the
REM double-click silently did nothing at all.
set "HERE=%~dp0.."
if "%~1"=="--watch" goto watch
if "%~1"=="--demo" set "DEMO=--demo"

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
    start "" pythonw "%HERE%\opencode_pet.py" %DEMO%
) else (
    python "%HERE%\opencode_pet.py" %DEMO%
)
exit /b %ERRORLEVEL%

:watch
where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
    start "" pythonw "%HERE%\pet_watcher.py"
) else (
    python "%HERE%\pet_watcher.py"
)
exit /b %ERRORLEVEL%
