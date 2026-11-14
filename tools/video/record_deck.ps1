# Record pitch-deck slides (web/ served by tools/video/serve_deck.sh on :8772) for the demo video.
# Each clip loads the slide before, then presses ArrowRight at 0.4 s so the slide's entrance plays.
#   powershell -File tools\video\record_deck.ps1 [-Work ...] [-Slides "2:13,1:14"]   (slide:seconds)
param(
  [string]$Work = "$env:LOCALAPPDATA\Temp\onset-video\work-v2",
  [string]$Slides = '2:14,1:13,8:8,9:10,10:10,7:19,12:15,13:20',
  [string]$Base = 'http://127.0.0.1:8772/pitch/index.html'
)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:NODE_PATH) { $env:NODE_PATH = "$env:LOCALAPPDATA\Temp\onset-video\node_modules" }
foreach ($pair in $Slides.Split(',')) {
  $n, $s = $pair.Split(':')
  $prev = [int]$n - 1
  if ($prev -lt 1) { $url = "$Base#slide-$n"; $keys = '' } else { $url = "$Base#slide-$prev"; $keys = '0.4:ArrowRight' }
  node "$here\record.cjs" --url $url --out "$Work\deck-$n" --seconds $s --pre 2 --keys $keys
}
