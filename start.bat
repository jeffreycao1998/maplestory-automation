@echo off
setlocal EnableExtensions DisableDelayedExpansion
title MapleBot
rem Double-click this file to install (first time) and start MapleBot.
cd /d "%~dp0"

rem ---- 1. Run as administrator --------------------------------------------
rem net session needs the Server service; fltmc is a second check that doesn't.
net session >nul 2>&1
if not errorlevel 1 goto :is_admin
fltmc >nul 2>&1
if not errorlevel 1 goto :is_admin
rem Already tried to elevate once: don't loop.
if /i "%~1"=="--elevated" goto :elevate_failed

echo MapleBot needs admin rights: the game runs as admin, so the bot must too or Windows blocks its key presses.
rem The path goes to PowerShell through an environment variable, never inside the command
rem text, so spaces, quotes, apostrophes and brackets in the folder name can't break it.
set "MAPLEBOT_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath $env:MAPLEBOT_SELF -ArgumentList '--elevated' -Verb RunAs"
if errorlevel 1 goto :elevate_failed
exit /b 0

:elevate_failed
echo.
echo Admin rights were not granted. Right-click start.bat and choose "Run as administrator".
pause
exit /b 1

:is_admin
if not exist "logs" mkdir "logs"

rem ---- 2. Find Python 3.10 or newer ---------------------------------------
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :have_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto :have_python

echo.
echo Python 3.10 or newer is needed and was not found.
echo.
echo   1. Your browser will open python.org. Click the yellow "Download Python" button.
echo   2. Run the installer and TICK "Add python.exe to PATH" at the bottom of the first screen.
echo   3. Click "Install Now" and wait until it finishes.
echo   4. Double-click start.bat again.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:have_python
rem ---- 3. First run: create the virtual environment and install -----------
if exist ".venv\Scripts\python.exe" goto :have_venv
echo First run: setting things up. This takes a few minutes, only once...
%PY% -m venv .venv
if errorlevel 1 goto :venv_failed
if exist ".venv\installed-pyproject.txt" del ".venv\installed-pyproject.txt"

:have_venv
if not exist ".venv\installed-pyproject.txt" goto :install
fc /b "pyproject.toml" ".venv\installed-pyproject.txt" >nul 2>&1
if not errorlevel 1 goto :launch

:install
echo Installing / updating MapleBot's parts...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :install_failed
copy /y "pyproject.toml" ".venv\installed-pyproject.txt" >nul

:launch
rem ---- 4. Launch the app (no console window) ------------------------------
rem The app writes logs\app.pid when it starts and removes it when it exits, so we can
rem tell whether *this* app came up (not just any pythonw.exe on the machine).
if not exist ".venv\Scripts\pythonw.exe" goto :fallback
if exist "logs\app.pid" del /q "logs\app.pid" >nul 2>&1
start "MapleBot" /b ".venv\Scripts\pythonw.exe" -m maplebot 1>>"logs\app.log" 2>&1

set /a WAITED=0
:wait_pid
if exist "logs\app.pid" goto :check_alive
if %WAITED% GEQ 15 goto :fallback
timeout /t 1 /nobreak >nul
set /a WAITED+=1
goto :wait_pid

:check_alive
rem Still running a moment later? (A crash right after start removes the pid file.)
timeout /t 2 /nobreak >nul
if not exist "logs\app.pid" goto :fallback
set "APPPID="
set /p APPPID=<"logs\app.pid"
if not defined APPPID goto :fallback
tasklist /fi "PID eq %APPPID%" /nh 2>nul | find "%APPPID%" >nul
if errorlevel 1 goto :fallback
exit /b 0

:fallback
echo.
echo The app didn't start in the background. Starting it here so you can see the error:
echo.
".venv\Scripts\python.exe" -m maplebot
echo.
echo If you see an error above, copy it (and logs\app.log) when asking for help.
pause
exit /b 1

:venv_failed
echo.
echo Couldn't create the .venv folder. Make sure this folder isn't read-only
echo (for example, not inside "Program Files") and try again.
pause
exit /b 1

:install_failed
echo.
echo Installing failed. Check your internet connection and try again.
echo If it keeps failing, delete the .venv folder and run start.bat again.
pause
exit /b 1
