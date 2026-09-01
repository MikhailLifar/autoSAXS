@echo off
REM Double-click this file to install autoSAXS on Windows.
setlocal
cd /d "%~dp0"
set "LOG=%~dp0install-last-run.log"
set "PS1=%~dp0installer\Install-autoSAXS.ps1"
if not exist "%PS1%" set "PS1=%~dp0installer-windows\Install-autoSAXS.ps1"
if not exist "%PS1%" (
  echo Could not find the installer wizard script.
  echo Expected: installer\Install-autoSAXS.ps1
  pause
  exit /b 1
)
echo Running autoSAXS installer... > "%LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" >> "%LOG%" 2>&1
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" (
  echo.
  echo Installer failed with exit code %ERR%.
  echo Details were saved to:
  echo   %LOG%
  echo.
  type "%LOG%"
  echo.
  pause
)
endlocal & exit /b %ERR%
