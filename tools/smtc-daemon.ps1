# smtc-daemon.ps1 -- poll the Windows media session (SMTC) and write the state to a JSON file.
# QQ Music publishes an SMTC session, so this is how we read it.
#
# Why a file instead of stdout: when stdout is a pipe, PowerShell buffers Write-Output
# aggressively (we measured 0 lines in 8s), while file writes land immediately.
# ASCII-only on purpose (Windows PowerShell reads BOM-less .ps1 as ANSI).
param(
    [string]$OutFile = "",
    [int]$IntervalMs = 500,
    [int]$MaxIterations = 0        # 0 = run forever; set a small number for testing
)

if ([string]::IsNullOrWhiteSpace($OutFile)) {
    $root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
    $OutFile = Join-Path $root "_music.json"
}
$utf8 = New-Object System.Text.UTF8Encoding($false)

# 单实例：服务重启不会带走已经脱离的采集进程，所以靠命名互斥量避免堆一堆。
$mutex = New-Object System.Threading.Mutex($false, 'opencode-ui-smtc-daemon')
if (-not $mutex.WaitOne(0)) {
    exit 0
}

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    })[0]

function Await($op, [Type]$t) {
    $m = $asTaskGeneric.MakeGenericMethod($t)
    $nt = $m.Invoke($null, @($op))
    $nt.Wait(-1) | Out-Null
    $nt.Result
}

[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])

$iter = 0
while ($true) {
    try {
        $sessions = $mgr.GetSessions()
        $pick = $null
        foreach ($s in $sessions) {
            if ($s.SourceAppUserModelId -like 'QQMusic*') { $pick = $s; break }
        }
        if ($null -eq $pick) {
            foreach ($s in $sessions) {
                if ($s.GetPlaybackInfo().PlaybackStatus.ToString() -eq 'Playing') { $pick = $s; break }
            }
        }
        if ($null -eq $pick -and $sessions.Count -gt 0) { $pick = $sessions[0] }

        $o = [ordered]@{}
        $o.ts = [int64]([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())
        $o.sessions = @($sessions).Count
        if ($null -ne $pick) {
            $p = Await ($pick.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
            $info = $pick.GetPlaybackInfo()
            $tl = $pick.GetTimelineProperties()
            $o.app = [string]$pick.SourceAppUserModelId
            $o.status = [string]$info.PlaybackStatus.ToString()
            $o.title = [string]$p.Title
            $o.artist = [string]$p.Artist
            $o.album = [string]$p.AlbumTitle
            $o.pos = [math]::Round($tl.Position.TotalSeconds, 1)
            $o.end = [math]::Round($tl.EndTime.TotalSeconds, 1)
            $o.canPlay = [bool]($info.Controls.IsPlayEnabled -or $info.Controls.IsPauseEnabled)
            $o.canNext = [bool]$info.Controls.IsNextEnabled
            $o.canPrev = [bool]$info.Controls.IsPreviousEnabled
        }
        $json = $o | ConvertTo-Json -Compress
        [System.IO.File]::WriteAllText($OutFile, $json, $utf8)
    }
    catch {
        try { [System.IO.File]::WriteAllText($OutFile, '{"error":"' + $_.Exception.Message.Replace('"', "'") + '"}', $utf8) } catch { }
    }

    $iter++
    if ($MaxIterations -gt 0 -and $iter -ge $MaxIterations) { break }
    Start-Sleep -Milliseconds $IntervalMs
}
