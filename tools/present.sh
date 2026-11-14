#!/usr/bin/env bash
# Open the presentation for rehearsal: http://localhost:8765/present
#
#   tools/present.sh            starts the local dashboard server (no agent, no Chert line) and opens it
#   tools/present.sh --no-open  only starts the server
#
# On demo day use `tools/demo.sh --present` instead: it also starts the agent and connects the line,
# and the presentation's middle scene shows the live call. Here that scene shows the last run, a
# simulated one (`.venv/bin/python tools/demo_events.py --outcome escalate`) or the browser demo.
set -u
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-.venv/bin/python}; [ -x "$PY" ] || PY=$HOME/onset/.venv/bin/python
PORT=${PORT:-8765}
URL="http://localhost:$PORT/present"
if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  echo "[present] a dashboard is already running on port $PORT"
  STARTED=0
else
  "$PY" tools/dashboard.py --port "$PORT" & STARTED=$!
  for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 0.5; done
fi
echo "[present] $URL   (F full screen, N notes, ? keys, D jumps to the live demo)"
if [ "${1:-}" != --no-open ]; then
  if command -v cmd.exe >/dev/null 2>&1; then (cd /mnt/c 2>/dev/null && cmd.exe /c start "" "$URL" >/dev/null 2>&1) || true
  elif [ "$(uname -s)" = Darwin ]; then open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true; fi
fi
[ "$STARTED" = 0 ] || { trap 'kill $STARTED 2>/dev/null' INT TERM; wait "$STARTED"; }
