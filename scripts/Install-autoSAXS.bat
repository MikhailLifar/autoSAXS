@echo off
REM Double-click entry for the autoSAXS installer (Windows).
REM Does not require a global Python install — launches a PowerShell WinForms wizard.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-autoSAXS.ps1"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo Installer exited with code %ERR%.
  pause
)
endlocal & exit /b %ERR%
