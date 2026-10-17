@echo off
REM OpenCode Pet - start the pet without a console window.
REM Double-click this file, or run it from a terminal while developing.

setlocal
set "HERE=%~dp0"
if "%~1"=="--watch" goto watch
if "%~1"=="--demo" set "DEMO=--demo"

where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
    start "" pythonw "%HERE%opencode_pet.py" %DEMO%
) else (
    python "%HERE%opencode_pet.py" %DEMO%
)
exit /b %ERRORLEVEL%

:watch
where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
    start "" pythonw "%HERE%pet_watcher.py"
) else (
    python "%HERE%pet_watcher.py"
)
exit /b %ERRORLEVEL%
