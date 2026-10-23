@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Please install Python 3.10+ and add it to PATH.
  pause
  exit /b 1
)
python launcher.py
if errorlevel 1 pause
