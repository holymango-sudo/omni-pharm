@echo off
chcp 65001 >nul
title OMNI-PHARM 백업
echo OMNI-PHARM 백업을 시작합니다 (코드 변경 이력 + 데이터·모델·설정 복사)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\backup.ps1" -Full %*
echo.
echo 백업 위치: 프로그램 폴더 옆의 OMNI-PHARM_backups 폴더
echo 다른 드라이브(USB 등)에 백업하려면:  "OMNI-PHARM 백업.bat" -Target E:\OMNI-PHARM_backups
echo.
pause
