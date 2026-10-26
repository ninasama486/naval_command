@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist "%~dp0settings.json" (
    echo.
    echo 找不到 settings.json。
    echo 请确认项目文件完整，然后重新运行。
    echo.
    pause
    exit /b 1
)

echo.
echo 即将打开 settings.json。
echo 请填写 HOI4 安装目录、HOI4 用户目录等路径，保存并关闭记事本后会自动检查。
echo.
start "" /wait notepad "%~dp0settings.json"

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
    echo 没有找到 Python 3.10+。请先安装 Python，或把 python.exe 加入 PATH。
    echo 设置文件已经保存，但暂时无法自动检查路径。
    echo.
    pause
    exit /b 1
)

%PY% "%~dp0bridge\check_setup.py"
echo.
pause
