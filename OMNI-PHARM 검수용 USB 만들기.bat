@echo off
chcp 65001 >nul
title OMNI-PHARM 검수용 복사본 만들기
echo 다른 PC에서 검수받을 수 있게, 실행에 필요한 것만 USB(또는 다른 폴더)에 복사합니다.
echo   - 기본: 약 6.5GB (AI 복약 상담·처방전 사진은 정해진 문장으로 답함, 나머지 기능은 모두 같음)
echo   - AI 상담 포함: 약 17GB (USB가 exFAT 또는 NTFS여야 함 - FAT32는 4GB가 넘는 파일을 담지 못함)
echo   - 원본 다운로드 데이터(40GB), API 키, 계정, 약사 이름이 든 파일은 복사하지 않습니다.
echo.
set /p DRIVE=USB 드라이브 문자를 입력하세요 (예: E) :
set DRIVE=%DRIVE::=%
set /p AI=AI 상담 모델도 넣을까요? (Y/N) :
if /i "%AI%"=="Y" (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\make_review_package.ps1" -Target "%DRIVE%:\OMNI-PHARM" -WithAI
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\make_review_package.ps1" -Target "%DRIVE%:\OMNI-PHARM"
)
echo.
echo 복사가 끝나면 USB의 OMNI-PHARM 폴더를 검수자에게 전달하세요. 맨 위의 '0_먼저_읽어_주세요.txt'에 실행 방법이 있습니다.
pause
