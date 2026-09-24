@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 正在启动 opencode-ui ...
echo   . 确保面板服务在 127.0.0.1:8787 上运行
echo   . 以独立浏览器 profile 打开应用窗口（无地址栏，不干扰日常浏览器）
start "" pythonw "%~dp0..\backend\watch.py" --once
