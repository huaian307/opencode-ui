@echo off
REM Restrict the OpenCode redirect to D:\agentlist (others stay on C:), and
REM register the auto-heal scheduled tasks. CLOSE OpenCode / Canva / CC Switch first.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0opencode-only-in-agentlist.ps1"
echo.
echo Done. Log: D:\agentlist\opencode-only.log
pause
