@echo off
rem ============================================================
rem  One-click demo shutdown (double-click me!)
rem  Stops: backend (9000) + frontend (5173) + OpenMES/ERPNext
rem  container groups + business DB container.
rem  Containers are only STOPPED (not removed) - the next run of
rem  the launcher brings everything back in seconds.
rem ============================================================
title Auto Parts Agents - Demo Shutdown
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_demo.ps1"
echo.
echo All stopped. Double-click the launcher bat to start again.
pause
