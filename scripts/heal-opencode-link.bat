@echo off
REM Auto-heal the OpenCode junction (called by scheduled tasks or manually).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0heal-opencode-link.ps1"
