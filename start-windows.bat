@echo off
setlocal
cd /d "%~dp0"

where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Windows PowerShell was not found.
  echo Install Windows PowerShell 5.1 or PowerShell 7, then try again.
  pause
  exit /b 1
)

echo Starting Business Code Agent...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-windows.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Startup failed. Read the error above or .data\server-error.log.
  pause
)
exit /b %EXIT_CODE%
