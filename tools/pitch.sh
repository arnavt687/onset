#!/usr/bin/env bash
# Serve the pitch deck (web/pitch) on http://localhost:8770, offline-ready.
# Copies the MediaPipe models next to the deck (gitignored) so the live demo works without Wi-Fi.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p web/pitch/vendor/models web/pitch/media
for m in face_landmarker.task pose_landmarker_full.task; do
  src="${ONSET_MODELS:-models}/$m"; [ -f "$src" ] || src="$HOME/onset/models/$m"
  [ -f "web/pitch/vendor/models/$m" ] || { [ -f "$src" ] && cp "$src" web/pitch/vendor/models/; } || true
done
PORT="${1:-8770}"
echo "Onset deck: http://localhost:$PORT   (N notes, P presenter window, ? keys)"
exec python3 -m http.server "$PORT" --bind 127.0.0.1 --directory web/pitch
