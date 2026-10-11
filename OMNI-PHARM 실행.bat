@echo off
chcp 65001 >nul
title OMNI-PHARM
echo OMNI-PHARM 을 시작합니다. 잠시 기다려 주세요...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_servers.ps1"
echo.
echo 브라우저가 열리지 않으면 http://localhost:8000 에 접속하세요.
echo 이 창은 닫아도 프로그램은 계속 실행됩니다. 종료는 "OMNI-PHARM 종료.bat"
ping -n 9 127.0.0.1 >nul
