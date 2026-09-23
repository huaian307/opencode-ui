# smtc-control.ps1 -- send a transport command to the QQ Music (or current) media session.
# ASCII-only on purpose.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('playpause', 'play', 'pause', 'next', 'prev', 'stop')]
    [string]$Action
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
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

$pick = $null
foreach ($s in $mgr.GetSessions()) {
    if ($s.SourceAppUserModelId -like 'QQMusic*') { $pick = $s; break }
}
if ($null -eq $pick) {
    foreach ($s in $mgr.GetSessions()) {
        if ($s.GetPlaybackInfo().PlaybackStatus.ToString() -eq 'Playing') { $pick = $s; break }
    }
}

if ($null -eq $pick) {
    Write-Output '{"ok":false,"error":"no media session"}'
    exit 0
}

try {
    switch ($Action) {
        'playpause' { Await ($pick.TryTogglePlayPauseAsync()) ([bool]) | Out-Null }
        'play' { Await ($pick.TryPlayAsync()) ([bool]) | Out-Null }
        'pause' { Await ($pick.TryPauseAsync()) ([bool]) | Out-Null }
        'next' { Await ($pick.TrySkipNextAsync()) ([bool]) | Out-Null }
        'prev' { Await ($pick.TrySkipPreviousAsync()) ([bool]) | Out-Null }
        'stop' { Await ($pick.TryStopAsync()) ([bool]) | Out-Null }
    }
    Write-Output ('{"ok":true,"action":"' + $Action + '"}')
}
catch {
    Write-Output ('{"ok":false,"error":"' + $_.Exception.Message.Replace('"', '') + '"}')
}
