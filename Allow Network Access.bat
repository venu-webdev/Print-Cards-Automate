@echo off
REM Lets other PCs in the shop reach Card Print Studio (port 5000) through Windows Firewall,
REM on private / domain networks only (not public Wi-Fi). Asks for administrator permission.
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)
title Card Print Studio - allow network access
netsh advfirewall firewall delete rule name="Card Print Studio" >nul 2>&1
netsh advfirewall firewall add rule name="Card Print Studio" dir=in action=allow protocol=TCP localport=5000 profile=private,domain
echo.
echo Done. Turn on sharing in the website: Settings - Network sharing (then restart Start Website.bat).
pause
