# Install autostart: put a shortcut to launch.vbs in the Startup folder.
# (ASCII-only on purpose: Windows PowerShell reads BOM-less .ps1 as ANSI.)
$base = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'opencode-ui.lnk'

$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$s.Arguments = '"' + (Join-Path $base 'launch.vbs') + '"'
$s.WorkingDirectory = $base
$s.Description = 'opencode-ui panel autostart (watcher + app window)'
$s.Save()

Write-Host "Installed: $lnk"
Write-Host "To remove, run uninstall-startup.ps1"
