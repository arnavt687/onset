#!/usr/bin/env bash
# Serve this checkout's web/ folder read-only for capture: the pitch deck (/pitch/index.html) and
# the video cards (/video/index.html), on 127.0.0.1:8772.
#   bash tools/video/serve_deck.sh [dir] [port]
set -euo pipefail
DIR="${1:-$(cd "$(dirname "$0")/../.." && pwd)/web}"
exec python3 -m http.server "${2:-8772}" --bind 127.0.0.1 --directory "$DIR"
