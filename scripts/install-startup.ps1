# Install autostart: put a shortcut to the watcher in the Startup folder.
#
# The shortcut targets pythonw.exe directly (NOT wscript + launch.vbs):
#   - Microsoft is deprecating VBScript (KB5124010+ logs VBScriptDeprecationAlert);
#   - Windows Script Host reads .vbs as ANSI, so a non-ASCII comment can corrupt
#     the script and break autostart (see AGENTS.md pitfall #28).
#
# (ASCII-only on purpose: Windows PowerShell reads BOM-less .ps1 as ANSI.)
$scripts = Split-Path -Parent $MyInvocation.MyCommand.Path
$base = Split-Path -Parent $scripts
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup 'opencode-ui.lnk'

# Prefer the full path to pythonw.exe; fall back to the bare name (resolved via PATH).
$pyw = 'pythonw.exe'
try {
    $cmd = Get-Command pythonw.exe -ErrorAction Stop
    if ($cmd -and $cmd.Source) { $pyw = $cmd.Source }
} catch { }

$sh = New-Object -ComObject WScript.Shell
$s = $sh.CreateShortcut($lnk)
$s.TargetPath = $pyw
$s.Arguments = '"' + (Join-Path $base 'backend\watch.py') + '"'
$s.WorkingDirectory = $base
$s.Description = 'opencode-ui panel autostart (watcher + app window)'
$s.Save()

Write-Host "Installed: $lnk"
Write-Host "Target: $pyw"
Write-Host "Args:   $($s.Arguments)"
Write-Host "To remove, run uninstall-startup.ps1"
