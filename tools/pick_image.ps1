# pick_image.ps1 -- show a native Windows "open file" picker and write the result to a file.
# ASCII-only on purpose (Windows PowerShell reads BOM-less .ps1 as ANSI).
# The caller (server.py) passes -OutFile; an empty file means the user cancelled.
param(
    [Parameter(Mandatory = $true)][string]$OutFile
)

Add-Type -AssemblyName System.Windows.Forms

$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "Select an image"
$dialog.Filter = "Images (*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp;*.avif)|*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp;*.avif|All files (*.*)|*.*"
$dialog.CheckFileExists = $true
$dialog.CheckPathExists = $true
$dialog.Multiselect = $false

# Tiny invisible topmost owner so the dialog comes to the front of the panel.
$owner = New-Object System.Windows.Forms.Form
$owner.Text = "opencode-ui"
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
$owner.Width = 1
$owner.Height = 1
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.Opacity = 0
$owner.Show()

$result = $dialog.ShowDialog($owner)
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [System.IO.File]::WriteAllText($OutFile, $dialog.FileName)
} else {
    [System.IO.File]::WriteAllText($OutFile, "")
}

$owner.Close()
$owner.Dispose()
$dialog.Dispose()
