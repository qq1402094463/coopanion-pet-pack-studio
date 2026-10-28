@echo off
chcp 65001 >nul
title Pet Pack Studio - Coopanion
cd /d "%~dp0"
echo.
echo   Starting Pet Pack Studio...
echo   A browser window will open. Close this window to stop.
echo.
python server.py
if errorlevel 1 (
  echo.
  echo   Python failed to start. Install Python 3.10+ and try again.
  pause
)
