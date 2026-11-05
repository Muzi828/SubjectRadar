@echo off
chcp 65001 >nul
title 立即检查期刊更新
cd /d "%~dp0"
python app\sync_cli.py --trigger manual
echo.
pause
