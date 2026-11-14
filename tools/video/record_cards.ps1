# Record the title and end cards from web/video/index.html (serve <repo>/web on :8772 with
#   wsl -d Ubuntu -- bash tools/video/serve_deck.sh /home/arnav/onset-wt/video/web 8772).
param([string]$Work = "$env:LOCALAPPDATA\Temp\onset-video\work", [string]$Base = 'http://127.0.0.1:8772/video/index.html')
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $env:NODE_PATH) { $env:NODE_PATH = "$env:LOCALAPPDATA\Temp\onset-video\node_modules" }
node "$here\record.cjs" --url "$Base`?rec#title" --out "$Work\card-title" --seconds 8 --pre 1 --keys '0.3:r'
node "$here\record.cjs" --url "$Base`?rec#end" --out "$Work\card-end" --seconds 9 --pre 1 --keys '0.3:r'
