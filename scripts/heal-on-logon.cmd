@echo off
REM Run the OpenCode junction auto-heal at logon (per-user, no admin needed).
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\opencode-ui\scripts\heal-opencode-link.ps1"
