# -*- mode: powershell -*-
# Auto-heal: make sure %LOCALAPPDATA%\Programs\@opencodedesktop is a junction
# pointing to D:\agentlist\opencode\app. If an OpenCode self-update replaced the
# junction with a real folder on C:, merge it back to D: and re-link.
# Safe to run any time: if OpenCode is running from it, it skips and retries later.
#
# ASCII ONLY (AGENTS.md pitfall #9). Run manually or via the scheduled tasks
# OpenCodeLinkHeal-15min / OpenCodeLinkHeal-logon.

$L    = Join-Path $env:LOCALAPPDATA "Programs"
$Dest = Join-Path $L "@opencodedesktop"
$App  = "D:\agentlist\opencode\app"
$Log  = "D:\agentlist\heal-opencode.log"

function Log($m) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    try { New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null } catch {}
    try { Add-Content -Path $Log -Value $line -Encoding UTF8 } catch {}
}

function Is-Junction($p) {
    if (-not (Test-Path -LiteralPath $p)) { return $false }
    return ((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
}

if (-not (Test-Path -LiteralPath $App)) { exit 0 }   # nothing to link to

if (Is-Junction $Dest) {
    $t = (Get-Item -LiteralPath $Dest -Force).Target
    $t = if ($t -is [array]) { $t[0] } else { $t }
    if ($t -and ($t.TrimEnd('\') -ieq $App.TrimEnd('\'))) { exit 0 }   # already correct
    Log "junction points to '$t', re-linking to $App"
    & cmd /c rmdir "$Dest" | Out-Null
} elseif (Test-Path -LiteralPath $Dest) {
    # a real folder (an update replaced the junction). Only heal when not in use.
    $busy = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path -like "$Dest\*" })
    if ($busy.Count -gt 0) { Log "OpenCode running from C: copy - skip now, will retry"; exit 0 }
    Log "healing: merge real folder back to D:"
    & robocopy "$Dest" "$App" /E /COPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Log "robocopy FAILED rc=$LASTEXITCODE"; exit 4 }
    Remove-Item -LiteralPath $Dest -Recurse -Force
}

& cmd /c mklink /J "$Dest" "$App" | Out-Null
if (Is-Junction $Dest) { Log "healed: $Dest -> $App" } else { Log "FAILED to create junction" }
exit 0
