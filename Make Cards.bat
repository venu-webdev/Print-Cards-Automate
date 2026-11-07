@echo off
REM Double-click: converts every PDF in "raw" into Front/Back JPEGs in "export"
cd /d "%~dp0"
python make_cards.py %*
echo.
pause
