@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

call "%~dp0bridge\install.bat" %*
echo.
pause
