' opencode-ui/launch.vbs -- start the watcher with no visible window.
' The watcher keeps the panel server (127.0.0.1:8787) alive and opens the
' app window when OpenCode starts.
'
' IMPORTANT: keep this file ASCII-only.
' Windows Script Host reads .vbs using the system ANSI code page, so a
' BOM-less UTF-8 file with non-ASCII comments gets mis-decoded, which can
' swallow line breaks and corrupt the script (base becomes empty ->
' "The system cannot find the file specified" at sh.CurrentDirectory).
Option Explicit

Dim sh, fso, base, pyw, cmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

launcher = fso.GetParentFolderName(WScript.ScriptFullName)
base = fso.GetParentFolderName(launcher)
' Relies on PATH; if Python is not on PATH, set pyw to the full path of
' pythonw.exe instead, e.g. "D:\Python312\pythonw.exe".
pyw  = "pythonw.exe"

sh.CurrentDirectory = base
cmd = """" & pyw & """ """ & base & "\backend\watch.py"""
sh.Run cmd, 0, False             ' 0 = hidden window
