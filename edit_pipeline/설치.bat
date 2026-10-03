@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo  영상 자동 컷편집 - 설치 (처음 한 번만)
echo ========================================================
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython
%PY% --version
if errorlevel 1 goto nopython
echo.
echo [1/3] 가상환경 만들기...
%PY% -m venv .venv
if errorlevel 1 goto fail
echo [2/3] pip 업데이트...
".venv\Scripts\python.exe" -m pip install --upgrade pip
echo [3/3] 필요한 패키지 설치(몇 분 걸립니다)...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo.
echo 설치 완료! 이제 input 폴더에 촬영본을 넣고 실행.bat 을 더블클릭하세요.
pause
exit /b 0
:nopython
echo.
echo 파이썬이 없습니다. 열리는 페이지에서 Python 3.12 를 설치하세요.
echo 설치 첫 화면 아래 "Add python.exe to PATH" 를 꼭 체크한 뒤, 이 파일을 다시 실행하세요.
start "" "https://www.python.org/downloads/"
pause
exit /b 1
:fail
echo.
echo 설치 중 오류가 났습니다. 위 메시지를 캡처해서 알려 주세요.
pause
exit /b 1
