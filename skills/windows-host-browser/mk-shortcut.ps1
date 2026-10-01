$ErrorActionPreference = 'Stop'

# The opener script: new window if the debug Chrome is running, else start the
# scheduled task (which launches it windowless with the current debug port),
# wait for the port, and open the window.
$opener = @'
# The chromedebug task starts Chrome with --no-startup-window (so background
# launches stay invisible), so this shortcut must open the window itself.
$chrome = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
function Get-DebugChrome {
  Get-CimInstance Win32_Process -Filter "name='chrome.exe'" |
    Where-Object { $_.CommandLine -like '*chrome-debug*' }
}
if (-not (Get-DebugChrome)) {
  schtasks /run /tn chromedebug | Out-Null
  # Wait for the task's Chrome to own the profile: launching chrome.exe before
  # that would start a second Chrome without the debug port.
  $port = [regex]::Match((Get-ScheduledTask chromedebug).Actions[0].Arguments,
                         'remote-debugging-port=(\d+)').Groups[1].Value
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 "http://127.0.0.1:$port/json/version" | Out-Null; break } catch {}
  }
}
Start-Process $chrome -ArgumentList '--user-data-dir=C:\chrome-debug'
'@
Set-Content -Path 'C:\chrome-debug\open-debug-chrome.ps1' -Value $opener -Encoding UTF8

# The desktop folder name is Cyrillic; WScript.Shell chokes saving there, so
# build the .lnk in an ASCII path and move it.
$desktop = [Environment]::GetFolderPath('Desktop')
$tmpLnk = 'C:\chrome-debug\Agent Chrome.lnk'
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($tmpLnk)
# conhost --headless: with Windows Terminal as the default terminal,
# -WindowStyle Hidden alone still flashes a terminal window.
$lnk.TargetPath = "$env:WINDIR\System32\conhost.exe"
$lnk.Arguments = '--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\chrome-debug\open-debug-chrome.ps1'
$lnk.IconLocation = 'C:\Program Files\Google\Chrome\Application\chrome.exe,0'
$lnk.WindowStyle = 7
$lnk.Description = 'Open the debug Chrome that agents drive over CDP'
$lnk.Save()
Move-Item -Force -LiteralPath $tmpLnk -Destination (Join-Path $desktop 'Agent Chrome.lnk')
Write-Output "created: $(Join-Path $desktop 'Agent Chrome.lnk')"
