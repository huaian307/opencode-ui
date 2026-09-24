# Remove autostart.
$lnk = Join-Path ([Environment]::GetFolderPath('Startup')) 'opencode-ui.lnk'
if (Test-Path -LiteralPath $lnk) {
    Remove-Item -LiteralPath $lnk -Force
    Write-Host "Removed: $lnk"
} else {
    Write-Host "Not installed, nothing to do."
}
