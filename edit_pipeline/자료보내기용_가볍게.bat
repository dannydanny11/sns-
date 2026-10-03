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
  echo 촬영본 폴더를 이 아이콘 위로 끌어다 놓으세요.
  echo 영상은 180p 저화질 사본, 마이크는 FLAC 으로 작게 만들어 "보낼자료" 폴더에 모읍니다.
  pause
  exit /b 1
)
set /p MIN=각 파일 앞부분 몇 분만 담을까요? (전체면 엔터, 예: 20): 
if "%MIN%"=="" (
  ".venv\Scripts\python.exe" -m autocut pack "%~1"
) else (
  ".venv\Scripts\python.exe" -m autocut pack "%~1" --minutes %MIN%
)
start "" "%~dp0보낼자료"
pause
