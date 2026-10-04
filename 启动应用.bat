@echo off
rem ============================================================
rem  One-click application launcher (double-click me!)
rem  What it does:
rem    1. Starts ERPNext + OpenMES containers if they are down
rem    2. Starts backend (port 9000) + frontend (port 5173)
rem    3. Runs the real-system preflight and shows results
rem    4. Opens the application page in your browser
rem  Backend/frontend keep running in background after this
rem  window closes. To stop them, close the processes listening
rem  on ports 9000/5173 (or just reboot).
rem ============================================================
title Auto Parts Agents - Application Launcher
cd /d "%~dp0"
echo Starting application environment (this may take a while on first run)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_app.ps1"
if errorlevel 1 (
    echo.
    echo [FAILED] Preflight did not pass. Read the messages above,
    echo          fix the marked item, then double-click again.
) else (
    echo.
    echo Opening application page in browser...
    start "" http://127.0.0.1:5173/
)
echo.
echo You can close this window now (services keep running).
pause
