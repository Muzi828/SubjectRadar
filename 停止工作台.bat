@echo off
chcp 65001 >nul
title Stop SafeLens
cd /d "%~dp0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
  taskkill /PID %%a /F >nul 2>nul
)
echo SafeLens stopped.
timeout /t 2 >nul
