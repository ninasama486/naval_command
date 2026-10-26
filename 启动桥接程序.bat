@echo off
setlocal
cd /d "%~dp0"
call "%~dp0bridge\run_bridge.bat" %*
