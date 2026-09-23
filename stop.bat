@echo off
chcp 65001 >nul
echo 正在停止 opencode-ui（面板窗口 + 面板服务 + 守护进程）...

rem 1) 关掉面板的独立浏览器窗口（按专属 profile 识别，只动它自己的实例）
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*browser-profile*' -and $_.Name -match 'msedge|chrome' } | ForEach-Object { Write-Host ('  . close window PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem 2) 干掉占用 8788（守护进程锁）和 8787（面板服务）的进程 —— 谁占端口谁就是我们的
powershell -NoProfile -Command "foreach ($port in 8788,8787) { $p = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess; if ($p) { Write-Host ('  . stop port ' + $port + ' owner PID ' + $p); Stop-Process -Id $p -Force -ErrorAction SilentlyContinue } }"

rem 3) 兜底：把任务栏恢复常驻可见（守护被强制结束时跑不到它的退出钩子）
python "%~dp0taskbar.py" restore 2>nul

echo 完成。（OpenCode 本体不受影响；下次它启动时守护进程会重新拉起）
pause
