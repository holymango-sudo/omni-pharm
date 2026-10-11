@echo off
chcp 65001 >nul
title OMNI-PHARM (네트워크 공유)
echo 같은 네트워크(와이파이)의 휴대폰·태블릿·다른 PC에서 접속할 수 있게 시작합니다.
echo 보안을 위해 .env 파일에 OMNIPHARM_ACCESS_PIN=원하는PIN 을 넣어 두는 것을 권장합니다.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_servers.ps1" -Lan
echo.
echo 위에 표시된 LAN 주소를 다른 기기의 브라우저에 입력하세요.
pause
