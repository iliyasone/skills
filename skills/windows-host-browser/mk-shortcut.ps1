$ErrorActionPreference = 'Stop'

# The opener script: new window if the debug Chrome is running, else start the
# scheduled task (which launches it with the current debug port).
$opener = @'
$running = Get-CimInstance Win32_Process -Filter "name='chrome.exe'" |
           Where-Object { $_.CommandLine -like '*chrome-debug*' }
if ($running) {
  Start-Process 'C:\Program Files\Google\Chrome\Application\chrome.exe' `
    -ArgumentList '--user-data-dir=C:\chrome-debug'
} else {
  schtasks /run /tn chromedebug | Out-Null
}
'@
Set-Content -Path 'C:\chrome-debug\open-debug-chrome.ps1' -Value $opener -Encoding UTF8

# The desktop folder name is Cyrillic; WScript.Shell chokes saving there, so
# build the .lnk in an ASCII path and move it.
$desktop = [Environment]::GetFolderPath('Desktop')
$tmpLnk = 'C:\chrome-debug\Agent Chrome.lnk'
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($tmpLnk)
$lnk.TargetPath = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\chrome-debug\open-debug-chrome.ps1'
$lnk.IconLocation = 'C:\Program Files\Google\Chrome\Application\chrome.exe,0'
$lnk.WindowStyle = 7
$lnk.Description = 'Open the debug Chrome that agents drive over CDP'
$lnk.Save()
Move-Item -Force -LiteralPath $tmpLnk -Destination (Join-Path $desktop 'Agent Chrome.lnk')
Write-Output "created: $(Join-Path $desktop 'Agent Chrome.lnk')"
