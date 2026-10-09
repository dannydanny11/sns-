@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 설치.bat 을 실행하세요.
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" -m autocut menu
