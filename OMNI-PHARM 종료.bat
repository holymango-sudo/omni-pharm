@echo off
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_servers.ps1"
ping -n 4 127.0.0.1 >nul
