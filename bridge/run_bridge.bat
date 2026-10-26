@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
if exist "D:\AI\anaconda\python.exe" set "PY=D:\AI\anaconda\python.exe"
if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PY=python"
    )
)
if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 -c "import sys" >nul 2>nul
        if not errorlevel 1 set "PY=py -3"
    )
)

if not defined PY (
    echo.
    echo Python 3.10+ was not found.
    echo Install Python or add python.exe to PATH, then run this file again.
    echo.
    pause
    exit /b 1
)

echo Using Python: %PY%
echo.
%PY% navy_command_bridge.py %*

echo.
echo Bridge stopped.
pause
