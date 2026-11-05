@echo off
chcp 65001 >nul
title 安装 SafeLens 每日 08:00 检查
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\install_scheduler.ps1" -DailyTime "08:00"
echo.
echo 安装完成。按任意键关闭。
pause >nul
