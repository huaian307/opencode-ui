' opencode-ui/launch.vbs —— 无窗口启动守护进程
' 守护进程会：确保面板服务在跑 + 在 OpenCode 启动时打开独立应用窗口
Option Explicit

Dim sh, fso, base, pyw, cmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
' 依赖 PATH；若 Python 没加入 PATH，把 pyw 改成你的 pythonw 全路径（例如 C:\Python312\pythonw.exe）
pyw  = "pythonw.exe"

sh.CurrentDirectory = base
cmd = """" & pyw & """ """ & base & "\watch.py"""
sh.Run cmd, 0, False             ' 0 = 隐藏窗口
