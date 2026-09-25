@echo off
REM Relocate %LOCALAPPDATA%\Programs to D:\agentlist\Programs (junction) so upgrades land on D:.
REM CLOSE OpenCode, Canva, CC Switch and anything else under Programs first.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0move-programs-to-d.ps1"
echo.
echo Done. Log: D:\agentlist\move-programs.log
pause
