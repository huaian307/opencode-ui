@echo off
REM Move the C: OpenCode install/data to D:\agentlist and leave junctions behind.
REM See move-opencode-to-d.ps1 for details. CLOSE OpenCode first (files are locked).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0move-opencode-to-d.ps1"
echo.
echo Done. Log: D:\agentlist\move-opencode.log
pause
