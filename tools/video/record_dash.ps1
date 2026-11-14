# Record the dashboard while tools/video/demo_run.sh plays each synthetic outcome (Windows side).
#   powershell -File tools\video\record_dash.ps1 [-Work C:\...\onset-video\work] [-Outcomes escalate,no_change]
# Needs the video dashboard running (tools/video/dash.sh, port 8766). Nothing is sent anywhere.
param(
  [string]$Work = "$env:LOCALAPPDATA\Temp\onset-video\work-v2",
  [string[]]$Outcomes = @('escalate', 'no_change', 'unclear', 'first_run'),
  [string]$Port = '8766'
)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
# This folder as a Linux path (the scripts live in WSL: \wsl.localhost\Ubuntu\home\...).
$linux = ($here -replace '^\\wsl(\.localhost|\$)\[^\]+', '') -replace '\', '/'
if (-not $env:NODE_PATH) { $env:NODE_PATH = "$env:LOCALAPPDATA\Temp\onset-video\node_modules" }
foreach ($o in $Outcomes) {
  # ?voice=0: no "click to enable sound" overlay over the page.
  node "$here\record.cjs" --url "http://127.0.0.1:$Port/?voice=0" --out "$Work\dash-$o" --dsf 1.5 --seconds 10 --init "$here\dash_video.js" `
    --run "wsl.exe -d Ubuntu -- bash $linux/demo_run.sh $o 1 8" --run-at 2 --tail 1
}
