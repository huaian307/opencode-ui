' opencode-ui/launch.vbs —— 无窗口启动守护进程
' 守护进程会：确保面板服务在跑 + 在 OpenCode 启动时打开独立应用窗口
Option Explicit

Dim sh, fso, base, pyw, cmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
pyw  = "D:\Python312\pythonw.exe"
If Not fso.FileExists(pyw) Then
    pyw = "pythonw.exe"          ' 退回 PATH
End If

sh.CurrentDirectory = base
cmd = """" & pyw & """ """ & base & "\watch.py"""
sh.Run cmd, 0, False             ' 0 = 隐藏窗口
