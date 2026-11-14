# Record the dashboard's Trend drawer for the demo video's longitudinal shot.
# Needs the video dashboard on :8766 and two weeks of demo history in its runs dir:
#   wsl -d Ubuntu -- bash -lc "cd ~/onset-wt/video-v2 && ~/onset/.venv/bin/python tools/demo_history.py --runs ~/onset/runs/video/dash-v2"
param([string]$Work = "$env:LOCALAPPDATA\Temp\onset-video\work-v2", [string]$Port = '8766')
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:NODE_PATH) { $env:NODE_PATH = "$env:LOCALAPPDATA\Temp\onset-video\node_modules" }
node "$here\record.cjs" --url "http://127.0.0.1:$Port/?voice=0&patient=demo-patient#trend" --out "$Work\dash-trend" --dsf 1.5 --seconds 12 --pre 0.5 --init "$here\dash_video.js"
