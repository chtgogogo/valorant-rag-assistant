@echo off
setlocal
rem ASCII only inside this file on purpose (safe under any console code page).
set "GUARD=%~dp0launch-guard.ps1"

if not exist "%GUARD%" (
  echo [ERROR] not found: "%GUARD%"
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%GUARD%" %*

echo.
echo Press any key to close this window.
pause >nul
