@echo off
chcp 65001 >nul
cd /d "%~dp0.."
if not exist logs mkdir logs
where python >nul 2>nul
if errorlevel 1 (
  py -3 app\sync_cli.py --trigger scheduled >> logs\scheduled-sync.log 2>&1
) else (
  python app\sync_cli.py --trigger scheduled >> logs\scheduled-sync.log 2>&1
)
