# -*- mode: powershell -*-
# Make upgrades land on D: permanently: relocate %LOCALAPPDATA%\Programs to
# D:\agentlist\Programs and leave a junction behind. Then the OpenCode installer
# (NSIS) writing to %LOCALAPPDATA%\Programs\@opencodedesktop physically writes on D:
# no matter whether it overwrites in place or wipes+recreates the folder.
#
# ASCII ONLY (AGENTS.md pitfall #9).
# Run AFTER closing OpenCode and every other app installed under Programs
# (Canva, CC Switch, ...). Double-click scripts\move-programs-to-d.bat.

$ErrorActionPreference = "Stop"

$Programs = Join-Path $env:LOCALAPPDATA "Programs"
$Dest     = "D:\agentlist\Programs"
$AppReal  = "D:\agentlist\opencode\app"          # OpenCode app (real dir today)
$AppInDest = Join-Path $Dest "@opencodedesktop"
$LogFile  = "D:\agentlist\move-programs.log"

function Log($m) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Host $line
    try { New-Item -ItemType Directory -Force -Path (Split-Path $LogFile) | Out-Null } catch {}
    try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 } catch {}
}

function Is-Junction($p) {
    if (-not (Test-Path -LiteralPath $p)) { return $false }
    $it = Get-Item -LiteralPath $p -Force
    return ($it.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
}

Log "=== relocate %LOCALAPPDATA%\Programs to D: ==="

# --- safety gate: nothing may be running from under Programs ---
$busy = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and ($_.Path -like "$Programs\*")
} | Select-Object -ExpandProperty Path -Unique)
if ($busy.Count -gt 0) {
    Log "ABORT: these are still running from Programs - close them and retry:"
    $busy | ForEach-Object { Log "    $_" }
    exit 3
}

# --- already done? ---
if (Is-Junction $Programs) {
    Log "Programs is already a junction; nothing to do."
    exit 0
}
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

# --- 1) drop the @opencodedesktop junction so it is not dragged along ---
$link = Join-Path $Programs "@opencodedesktop"
if (Is-Junction $link) {
    & cmd /c rmdir "$link" | Out-Null
    Log "removed junction: $link"
} elseif (Test-Path -LiteralPath $link) {
    Log "note: $link is a REAL dir (an upgrade likely replaced the junction). It will be moved to D: as-is."
}

# --- 2) move the real OpenCode app into D:\agentlist\Programs\@opencodedesktop ---
if ((Test-Path -LiteralPath $AppReal) -and -not (Is-Junction $AppReal) -and -not (Test-Path -LiteralPath $AppInDest)) {
    Log "moving $AppReal  ->  $AppInDest"
    Move-Item -LiteralPath $AppReal -Destination $AppInDest
} else {
    Log "skip app move (AppReal missing/junction, or dest already exists)"
}

# --- 3) move the rest of Programs (Canva, CC Switch, ...) ---
Log "moving remaining contents: $Programs  ->  $Dest"
& robocopy $Programs $Dest /E /MOVE /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { Log ("robocopy FAILED rc={0}" -f $LASTEXITCODE); exit 4 }

# --- 4) remove leftover Programs shell, then junction it to D: ---
if (Test-Path -LiteralPath $Programs) {
    & cmd /c rmdir /S /Q "$Programs" | Out-Null
}
if (Test-Path -LiteralPath $Programs) {
    Log "ABORT: could not remove $Programs (still in use)."
    exit 5
}
& cmd /c mklink /J "$Programs" "$Dest" | Out-Null
if (Is-Junction $Programs) { Log "junction OK: $Programs -> $Dest" } else { Log "junction FAILED"; exit 6 }

# --- 5) keep the tidy D:\agentlist\opencode\app path as a link too ---
if ((Test-Path -LiteralPath $AppInDest) -and -not (Test-Path -LiteralPath $AppReal)) {
    & cmd /c mklink /J "$AppReal" "$AppInDest" | Out-Null
    if (Is-Junction $AppReal) { Log "junction OK: $AppReal -> $AppInDest" } else { Log "tidy-path junction FAILED" }
}

Log "=== done ==="
Log "Reopen OpenCode with the desktop shortcut. The pending 2.0.16 update will now land on D:."
exit 0
