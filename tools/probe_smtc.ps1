# Probe Windows SMTC (System Media Transport Controls) sessions. ASCII-only on purpose:
# Windows PowerShell reads BOM-less .ps1 as ANSI, so keep this file ASCII.
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) {
    $m = $asTaskGeneric.MakeGenericMethod($t)
    $nt = $m.Invoke($null, @($op))
    $nt.Wait(-1) | Out-Null
    $nt.Result
}

[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])

Write-Output ("SMTC_API_OK sessions=" + $mgr.GetSessions().Count)
foreach ($s in $mgr.GetSessions()) {
    $props = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
    $info = $s.GetPlaybackInfo()
    $tl = $s.GetTimelineProperties()
    Write-Output ("APP=" + $s.SourceAppUserModelId)
    Write-Output ("  STATUS=" + $info.PlaybackStatus + " CONTROLS=" + $info.Controls.IsPlayEnabled + "/" + $info.Controls.IsNextEnabled)
    Write-Output ("  TITLE=" + $props.Title)
    Write-Output ("  ARTIST=" + $props.Artist)
    Write-Output ("  ALBUM=" + $props.AlbumTitle)
    Write-Output ("  POS=" + [math]::Round($tl.Position.TotalSeconds, 1) + " END=" + [math]::Round($tl.EndTime.TotalSeconds, 1))
}
