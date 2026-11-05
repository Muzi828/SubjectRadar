@echo off
cd /d "%~dp0"
start "" wscript.exe "%~dp0scripts\launch_workbench.vbs"
exit /b 0
