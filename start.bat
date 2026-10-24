@echo off
setlocal EnableExtensions
title MapleBot
rem Double-click this file to install (first time) and start MapleBot.
cd /d "%~dp0"

rem ---- 1. Run as administrator --------------------------------------------
net session >nul 2>&1
if errorlevel 1 (
    echo MapleBot needs admin rights: the game runs as admin, so the bot must too or Windows blocks its key presses.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
    if errorlevel 1 (
        echo.
        echo Admin rights were not granted. Right-click start.bat and choose "Run as administrator".
        pause
    )
    exit /b
)

if not exist "logs" mkdir "logs"

rem ---- 2. Find Python 3.10 or newer ---------------------------------------
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
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
)

rem ---- 3. First run: create the virtual environment and install -----------
if not exist ".venv\Scripts\python.exe" (
    echo First run: setting things up. This takes a few minutes, only once...
    %PY% -m venv .venv
    if errorlevel 1 goto :venv_failed
    if exist ".venv\installed-pyproject.txt" del ".venv\installed-pyproject.txt"
)

set "NEED_INSTALL=1"
if exist ".venv\installed-pyproject.txt" (
    fc /b "pyproject.toml" ".venv\installed-pyproject.txt" >nul 2>&1
    if not errorlevel 1 set "NEED_INSTALL=0"
)
if "%NEED_INSTALL%"=="1" (
    echo Installing / updating MapleBot's parts...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -e .
    if errorlevel 1 goto :install_failed
    copy /y "pyproject.toml" ".venv\installed-pyproject.txt" >nul
)

rem ---- 4. Launch the app (no console window) ------------------------------
if exist ".venv\Scripts\pythonw.exe" (
    start "MapleBot" /b ".venv\Scripts\pythonw.exe" -m maplebot 1>>"logs\app.log" 2>&1
    if not errorlevel 1 (
        rem Give it a moment; if it dies straight away, show the error instead.
        timeout /t 3 /nobreak >nul
        tasklist /fi "imagename eq pythonw.exe" 2>nul | find /i "pythonw.exe" >nul
        if not errorlevel 1 exit /b 0
    )
)

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
echo (e.g. not inside "Program Files") and try again.
pause
exit /b 1

:install_failed
echo.
echo Installing failed. Check your internet connection and try again.
echo If it keeps failing, delete the .venv folder and run start.bat again.
pause
exit /b 1
