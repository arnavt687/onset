# Make the Onset demo video end to end (Windows PowerShell; the repo is in WSL).
#
#   powershell -ExecutionPolicy Bypass -File tools\video\make.ps1 [-Steps setup,servers,tts,captions,record,build]
#
# setup     npm install playwright + ffmpeg-static into %LOCALAPPDATA%\Temp\onset-video (uses installed Chrome)
# servers   start the video dashboard (:8766, its own runs dir) and serve web/ (:8772) inside WSL
# tts       ElevenLabs voice-over from narration.json (cached per segment; prints characters used)
# captions  render the caller's caption frames (tools/render_captions.py)
# record    dashboard runs (4 outcomes), deck slides, title and end cards (into work-v2)
# build     cut the MP4 (timeline.cjs) -> ~/onset/runs/video/onset-demo-v2.mp4 and %USERPROFILE%\Videos
param([string[]]$Steps = @('setup', 'servers', 'tts', 'captions', 'record', 'build'))
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$linux = ($here -replace '^\\wsl(\.localhost|\$)\[^\]+', '') -replace '\', '/'
$repo = $linux -replace '/tools/video$', ''
$scratch = "$env:LOCALAPPDATA\Temp\onset-video"
$env:NODE_PATH = "$scratch\node_modules"
$env:FONTCONFIG_FILE = "$scratch\fonts.conf"
if ($Steps -contains 'setup') {
  New-Item -ItemType Directory -Force $scratch | Out-Null
  Push-Location $scratch
  if (-not (Test-Path package.json)) { npm init -y | Out-Null }
  $env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = '1'
  npm install --no-audit --no-fund playwright@1.49.1 ffmpeg-static@5.2.0 ffprobe-static@3.1.0
  Pop-Location
  "<?xml version=`"1.0`"?><fontconfig><dir>C:/Windows/Fonts</dir><cachedir>$($scratch -replace '\','/')/fccache</cachedir></fontconfig>" |
    Set-Content -Encoding ascii "$scratch\fonts.conf"
}
if ($Steps -contains 'servers') {
  wsl.exe -d Ubuntu -- bash -lc "cd ~ && (ONSET_VIDEO_RUNS=~/onset/runs/video/dash-v2 setsid nohup bash $linux/dash.sh 8766 >/tmp/onset-video-dash.log 2>&1 &) ; (setsid nohup bash $linux/serve_deck.sh $repo/web 8772 >/tmp/onset-video-web.log 2>&1 &) ; sleep 1"
}
if ($Steps -contains 'tts') { wsl.exe -d Ubuntu -- bash -lc "cd $repo && ~/onset/.venv/bin/python tools/video/tts.py --out ~/onset/runs/video/vo-v2" }
if ($Steps -contains 'captions') { wsl.exe -d Ubuntu -- bash -lc "cd $repo && ~/onset/.venv/bin/python tools/render_captions.py ~/onset/runs/video/captions-v2" }
if ($Steps -contains 'record') {
  & powershell -ExecutionPolicy Bypass -File "$here\record_dash.ps1"
  & powershell -ExecutionPolicy Bypass -File "$here\record_deck.ps1" -Slides '2:15,1:19,8:10,9:10,10:10,7:18,12:22,13:16'
  & powershell -ExecutionPolicy Bypass -File "$here\record_cards.ps1"
}
if ($Steps -contains 'build') { node "$here\build.cjs" }
