@echo off
setlocal EnableExtensions DisableDelayedExpansion
title MapleBot
rem Double-click this file to install (first time) and start MapleBot.
rem Control flow uses goto only (no parenthesised blocks), so brackets in the folder
rem name can't break it. Every label below appears exactly once.
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
rem ---- 3. First run: create the virtual environment ----------------------
if exist ".venv\Scripts\python.exe" goto :have_venv
echo First run: setting things up. This takes a few minutes, only once...
%PY% -m venv .venv
if errorlevel 1 goto :venv_failed
if exist ".venv\installed-pyproject.txt" del ".venv\installed-pyproject.txt"

:have_venv
rem Install only when pyproject.toml differs from the copy saved after the last install.
if not exist ".venv\installed-pyproject.txt" goto :install
fc /b "pyproject.toml" ".venv\installed-pyproject.txt" >nul 2>&1
if not errorlevel 1 goto :launch

:install
echo Installing / updating MapleBot's parts...
rem Hold the installation lock during pip, too: a second launcher must not
rem install dependencies while the update helper is replacing app files.
".venv\Scripts\python.exe" "src\maplebot\app\updater.py" --install-dependencies "%CD%"
if errorlevel 3 goto :install_busy
if errorlevel 1 goto :install_failed

:launch
rem ---- 4. Launch the app (no console window) ------------------------------
rem The app holds logs\app.lock while open (a second copy says "already open" and exits)
rem and writes its PID to logs\app.pid, so we can tell whether *this* app came up.
set "MAPLEBOT_PYW=%CD%\.venv\Scripts\pythonw.exe"
if not exist ".venv\Scripts\pythonw.exe" goto :fallback

call :pid_alive
if "%PID_ALIVE%"=="1" goto :already_open
rem Stale pid file (app crashed or was killed): safe to remove.
if exist "logs\app.pid" del /q "logs\app.pid" >nul 2>&1
start "MapleBot" /b ".venv\Scripts\pythonw.exe" -m maplebot 1>>"logs\app.log" 2>&1

set /a WAITED=0
:wait_pid
call :pid_alive
if "%PID_ALIVE%"=="1" goto :started
if %WAITED% GEQ 15 goto :slow_start
timeout /t 1 /nobreak >nul
set /a WAITED+=1
goto :wait_pid

:started
rem Still running a moment later? (A crash right after start removes the pid file.)
timeout /t 2 /nobreak >nul
call :pid_alive
if "%PID_ALIVE%"=="1" exit /b 0
goto :fallback

:slow_start
rem No pid file after 15 s. If the background copy is still alive it's just slow
rem (first start, antivirus scan): keep waiting instead of starting a second copy.
call :pyw_alive
if "%PYW_ALIVE%"=="0" goto :fallback
echo MapleBot is taking a while to open, please wait...
set /a WAITED=0

:wait_slow
call :pid_alive
if "%PID_ALIVE%"=="1" exit /b 0
call :pyw_alive
if "%PYW_ALIVE%"=="0" goto :fallback
if %WAITED% GEQ 90 goto :still_starting
timeout /t 1 /nobreak >nul
set /a WAITED+=1
goto :wait_slow

:still_starting
echo.
echo MapleBot is still starting in the background. If no window appears, check logs\app.log.
pause
exit /b 0

:already_open
echo MapleBot is already open.
rem Let the app show its own "already open" message; that copy exits straight away
rem without touching the running copy's files.
start "MapleBot" /b ".venv\Scripts\pythonw.exe" -m maplebot
exit /b 0

:fallback
rem Never start a console copy while the background one is still alive.
call :pyw_alive
if "%PYW_ALIVE%"=="1" goto :still_starting
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

:install_busy
echo.
echo MapleBot is open or updating. Close the other copy or wait for the update to finish.
pause
exit /b 3

:install_failed
echo.
echo Installing failed. Check your internet connection and try again.
echo If it keeps failing, delete the .venv folder and run start.bat again.
pause
exit /b 1

rem ---- helpers, used with "call" (each ends with exit /b) -------------------
:pid_alive
rem PID_ALIVE=1 if logs\app.pid names a running Python process.
set "PID_ALIVE=0"
if not exist "logs\app.pid" exit /b 0
set "APPPID="
set /p APPPID=<"logs\app.pid"
if not defined APPPID exit /b 0
echo %APPPID%| findstr /r /x "[0-9][0-9]*" >nul
if errorlevel 1 exit /b 0
tasklist /fi "PID eq %APPPID%" /nh 2>nul | find /i "python" >nul
if not errorlevel 1 set "PID_ALIVE=1"
exit /b 0

:pyw_alive
rem PYW_ALIVE=1 if this folder's .venv pythonw is running MapleBot. The path reaches
rem PowerShell through an environment variable, so odd folder names can't break it.
set "PYW_ALIVE=0"
if not defined MAPLEBOT_PYW exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = [IO.Path]::GetFullPath($env:MAPLEBOT_PYW); if (Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $p -and $_.CommandLine -like '*maplebot*' }) { exit 0 } else { exit 1 }" >nul 2>&1
if not errorlevel 1 set "PYW_ALIVE=1"
exit /b 0
