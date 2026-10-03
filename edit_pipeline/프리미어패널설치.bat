@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================================
echo  프리미어 프로 패널 설치 (처음 한 번만)
echo ========================================================
set "DEST=%APPDATA%\Adobe\CEP\extensions\kr.autocut.panel"
if exist "%DEST%" rmdir /s /q "%DEST%"
xcopy /E /I /Y /Q "%~dp0premiere_panel" "%DEST%" >nul
if errorlevel 1 goto fail
set "ROOT=%~dp0"
set "ROOT=%ROOT:\=/%"
if "%ROOT:~-1%"=="/" set "ROOT=%ROOT:~0,-1%"
> "%DEST%\config.js" echo window.AUTOCUT_ROOT = "%ROOT%";
rem 서명 안 된 패널을 쓸 수 있게(프리미어 버전별 설정)
for %%v in (9 10 11 12 13) do reg add "HKCU\Software\Adobe\CSXS.%%v" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul
echo.
echo 설치 완료!
echo  1) 프리미어 프로를 (켜져 있으면 껐다가) 다시 켭니다.
echo  2) 메뉴 [창] - [확장] - [영상 자동 컷편집] 을 엽니다.
echo  3) 결과마다 [프로젝트 만들기] 를 누르거나, "새 결과 자동으로 프로젝트 만들기" 를 켜 두세요.
pause
exit /b 0
:fail
echo.
echo 설치 중 오류가 났습니다. 위 메시지를 캡처해서 알려 주세요.
pause
exit /b 1
