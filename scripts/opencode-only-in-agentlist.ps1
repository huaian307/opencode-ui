# -*- mode: powershell -*-
# Restructure so ONLY OpenCode is redirected into D:\agentlist, and every other
# per-user app keeps installing to C:\...\Local\Programs (as before).
#
# Result:
#   C:\Users\...\Local\Programs\            (real dir, back on C:)
#       Canva\ CC Switch\ Common\ ...
#       @opencodedesktop\  =Junction=>  D:\agentlist\opencode\app
#   D:\agentlist\  = deepseekharness\ + opencode\   (no more Programs\)
#
# Also registers a scheduled task that auto-heals the junction if an OpenCode
# self-update replaces it with a real folder.
#
# ASCII ONLY (AGENTS.md pitfall #9). CLOSE OpenCode (and Canva / CC Switch) first.
# Run: double-click scripts\opencode-only-in-agentlist.bat

$ErrorActionPreference = "Stop"

$L     = Join-Path $env:LOCALAPPDATA "Programs"
$App   = "D:\agentlist\opencode\app"
$Progs = "D:\agentlist\Programs"
$Log   = "D:\agentlist\opencode-only.log"
$Heal  = "D:\opencode-ui\scripts\heal-opencode-link.ps1"

function Log($m) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Host $line
    try { New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null } catch {}
    try { Add-Content -Path $Log -Value $line -Encoding UTF8 } catch {}
}

function Is-Junction($p) {
    if (-not (Test-Path -LiteralPath $p)) { return $false }
    return ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
}

# Cross-volume directory move is flaky with Move-Item (locked shell-ext DLLs etc.);
# robocopy /E /MOVE is reliable (rc 0..7 = success).
function Move-Dir($src, $dst) {
    Log "moving $src -> $dst"
    & robocopy "$src" "$dst" /E /MOVE /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Log ("robocopy FAILED rc={0} for {1}" -f $LASTEXITCODE, $src); return $false }
    if (Test-Path -LiteralPath $src) { Remove-Item -LiteralPath $src -Recurse -Force -ErrorAction SilentlyContinue }
    return $true
}

Log "=== restrict OpenCode redirect to D:\agentlist ==="

# safety gate: nothing may be running from either Programs location
$busy = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and ($_.Path -like "$L\*" -or $_.Path -like "$Progs\*")
} | Select-Object -ExpandProperty Path -Unique)
if ($busy.Count -gt 0) {
    Log "ABORT: still running - close them and retry:"
    $busy | ForEach-Object { Log "    $_" }
    exit 3
}

# 1) drop the junction at D:\agentlist\opencode\app (target is under Progs)
if (Is-Junction $App) { & cmd /c rmdir "$App" | Out-Null; Log "removed junction: $App" }

# 2) move the real OpenCode program into agentlist
if (Test-Path -LiteralPath "$Progs\@opencodedesktop") {
    Move-Dir "$Progs\@opencodedesktop" $App | Out-Null
} else { Log "note: $Progs\@opencodedesktop not found (already moved?)" }

# 3) remove the C: Programs junction, then recreate it as a REAL folder
if (Is-Junction $L) { & cmd /c rmdir "$L" | Out-Null; Log "removed junction: $L" }
if (-not (Test-Path -LiteralPath $L)) { New-Item -ItemType Directory -Force -Path $L | Out-Null; Log "created real dir: $L" }

# 4) move the other per-user apps back to C:
foreach ($n in @("Canva", "CC Switch", "Common")) {
    if (Test-Path -LiteralPath "$Progs\$n") { Move-Dir "$Progs\$n" (Join-Path $L $n) | Out-Null }
}

# 5) drop the now-empty Programs folder
if (Test-Path -LiteralPath $Progs) {
    $left = @(Get-ChildItem -LiteralPath $Progs -Force)
    if ($left.Count -eq 0) { Remove-Item -LiteralPath $Progs -Force; Log "removed empty: $Progs" }
    else { Log ("WARN: $Progs not empty, left: " + (($left | ForEach-Object { $_.Name }) -join ", ")) }
}

# 6) recreate the single junction for OpenCode
& cmd /c mklink /J "$L\@opencodedesktop" "$App" | Out-Null
if (Is-Junction "$L\@opencodedesktop") { Log "junction OK: $L\@opencodedesktop -> $App" } else { Log "junction FAILED"; exit 6 }

# 7) self-heal no longer uses a scheduled task: launchers/launch_opencode.py runs
#    scripts/heal_opencode_link.py (hidden) right before starting OpenCode.
#    Remove any task left behind by older versions of this installer.
foreach ($tn in @("OpenCodeLinkHeal-15min", "OpenCodeLinkHeal-logon")) {
    try { Unregister-ScheduledTask -TaskName $tn -Confirm:$false -ErrorAction SilentlyContinue } catch {}
}
Log "self-heal moved to the launcher (no scheduled task)"

Log "=== done ==="
Log "Reopen OpenCode with the desktop shortcut. Other apps now install to C: as before."
exit 0
