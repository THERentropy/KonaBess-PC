@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    if exist "%~dp0dist\KonaBessPC.exe" (
        start "" "%~dp0dist\KonaBessPC.exe"
        exit /b 0
    )
    echo.
    echo 未检测到 Python，请安装 Python 3.9 或更高版本：
    echo   https://www.python.org/downloads/
    echo 安装时请勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

%PY% "%~dp0main.py" %*
if errorlevel 1 pause
