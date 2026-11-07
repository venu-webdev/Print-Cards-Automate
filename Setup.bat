@echo off
REM One-time setup on a new PC: installs the Python packages Card Print Studio needs.
cd /d "%~dp0"
title Card Print Studio - setup
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed.
  echo Install Python 3.12 from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo then run this file again.
  pause
  exit /b 1
)
python --version
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Package install failed - check the messages above.
  pause
  exit /b 1
)
if not exist raw mkdir raw
if not exist export mkdir export
echo.
echo Setup finished. Double-click "Start Website.bat" to run Card Print Studio.
pause
