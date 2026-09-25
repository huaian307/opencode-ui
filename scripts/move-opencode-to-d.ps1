# -*- mode: powershell -*-
# Move the per-user OpenCode desktop install/data to D:\agentlist and leave
# directory junctions at the original C: paths so every existing path keeps working.
#
# ASCII ONLY on purpose (see AGENTS.md pitfall #9): non-ASCII in .ps1 breaks parsing.
#
# Usage (close OpenCode / this panel first, then run):
#     powershell -NoProfile -ExecutionPolicy Bypass -File scripts\move-opencode-to-d.ps1
# or double-click scripts\move-opencode-to-d.bat

$ErrorActionPreference = "Stop"

$HomeDir = $env:USERPROFILE
$Root    = "D:\agentlist\opencode"
$LogFile = "D:\agentlist\move-opencode.log"

$Pairs = @(
    @{ Name = "app";   Src = (Join-Path $HomeDir "AppData\Local\Programs\@opencodedesktop"); Dst = (Join-Path $Root "app") },
    @{ Name = "data";  Src = (Join-Path $HomeDir "AppData\Roaming\ai.opencode.desktop");     Dst = (Join-Path $Root "data") },
    @{ Name = "share"; Src = (Join-Path $HomeDir ".local\share\opencode");                   Dst = (Join-Path $Root "share") },
    @{ Name = "state"; Src = (Join-Path $HomeDir ".local\state\opencode");                   Dst = (Join-Path $Root "state") }
)

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    try { New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null } catch {}
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Stop-OpenCode {
    $names = @("OpenCode", "opencode-cli", "opencode")
    $killed = @()
    foreach ($n in $names) {
        Get-Process -Name $n -ErrorAction SilentlyContinue | ForEach-Object {
            $killed += "$($_.ProcessName)#$($_.Id)"
            try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
    if ($killed.Count -gt 0) {
        Log ("stopped: " + ($killed -join ", "))
        Start-Sleep -Seconds 2
    } else {
        Log "OpenCode was not running"
    }
}

function Test-Junction($path) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $it = Get-Item -LiteralPath $path -Force
    return ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
}

Log "=== move OpenCode to D:\agentlist ==="
New-Item -ItemType Directory -Force -Path $Root | Out-Null

Stop-OpenCode

# Safety gate: if anything is still alive, abort BEFORE touching files
# (a half-finished move is far worse than not moving).
$still = @(Get-Process -Name "OpenCode", "opencode-cli", "opencode" -ErrorAction SilentlyContinue)
if ($still.Count -gt 0) {
    Log ("ABORT: OpenCode still running ({0}). Close it and run again." -f (($still | ForEach-Object { $_.ProcessName + "#" + $_.Id }) -join ", "))
    exit 3
}

$fail = 0
foreach ($p in $Pairs) {
    $src = $p.Src; $dst = $p.Dst; $name = $p.Name

    if (-not (Test-Path -LiteralPath $src)) {
        Log ("[{0}] source absent, skip: {1}" -f $name, $src)
        continue
    }
    if (Test-Junction $src) {
        Log ("[{0}] already a junction, skip: {1}" -f $name, $src)
        continue
    }
    if (Test-Path -LiteralPath $dst) {
        Log ("[{0}] DEST ALREADY EXISTS, skip: {1}" -f $name, $dst)
        $fail++
        continue
    }

    Log ("[{0}] moving {1}  ->  {2}" -f $name, $src, $dst)
    & robocopy $src $dst /E /MOVE /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) {
        Log ("[{0}] robocopy FAILED rc={1}" -f $name, $rc)
        $fail++
        continue
    }
    if (Test-Path -LiteralPath $src) {
        try { Remove-Item -LiteralPath $src -Recurse -Force } catch { Log ("[{0}] could not remove leftover: {1}" -f $name, $_.Exception.Message) }
    }

    & cmd /c mklink /J "$src" "$dst" | Out-Null
    if ((Test-Junction $src) -and (Test-Path -LiteralPath $dst)) {
        Log ("[{0}] junction OK" -f $name)
    } else {
        Log ("[{0}] junction FAILED" -f $name)
        $fail++
    }
}

Log ("=== done, failures: {0} ===" -f $fail)
if ($fail -eq 0) {
    Log "Reopen OpenCode with the desktop shortcut (OpenCode with panel)."
} else {
    Log "Some steps failed - check this log before reopening OpenCode."
}
exit $fail
