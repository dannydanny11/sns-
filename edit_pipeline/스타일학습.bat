@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 설치.bat 을 실행하세요.
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
if "%~1"=="" (
  echo 셀렉츠/프리미어/파이널컷에서 내보낸 XML 또는 FCPXML 파일을 이 아이콘 위로 끌어다 놓으세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m autocut learn "%~1"
pause
