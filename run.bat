@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :use_venv

where py >nul 2>nul
if not errorlevel 1 goto :use_py

where python >nul 2>nul
if not errorlevel 1 goto :use_python

echo 未找到 Python 3。请先安装 Python 3.10 或更高版本。
pause
goto :end

:use_venv
.venv\Scripts\python.exe server.py
if errorlevel 2 pause
goto :end

:use_py
py -3 server.py
if errorlevel 2 pause
goto :end

:use_python
python server.py
if errorlevel 2 pause

:end
endlocal
