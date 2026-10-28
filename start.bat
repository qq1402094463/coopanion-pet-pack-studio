@echo off
chcp 65001 >nul
title Pet Pack Studio for Coopanion
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo   [!] 没找到 python 命令。
  echo       装一个 Python 3.10 或更新版本: https://www.python.org/downloads/
  echo       安装时记得勾选 "Add python.exe to PATH"。
  echo.
  pause
  exit /b 1
)

python -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo   第一次运行，正在装 Pillow ...
  python -m pip install --quiet --upgrade pip
  python -m pip install --quiet pillow
)

python "studio\server.py"
if errorlevel 1 pause
