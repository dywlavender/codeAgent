@echo off
setlocal
cd /d "%~dp0"

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found.
  pause
  exit /b 1
)

echo Starting Business Code Agent (offline)...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher\start-offline-windows.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Startup failed. Read the error above or the offline log.
  pause
)
exit /b %EXIT_CODE%
