@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem Keep this file ASCII-only. Some Windows cmd versions misread UTF-8 batch
rem files that contain Chinese text and can close immediately when double-clicked.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Startup failed. Please send startup.log to technical support.
  pause
)

endlocal & exit /b %EXIT_CODE%
