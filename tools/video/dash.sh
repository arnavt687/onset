#!/usr/bin/env bash
# Serve the judge dashboard for the video on its own port and runs dir (does not touch runs/live).
#   bash tools/video/dash.sh [port]
set -euo pipefail
cd "$(dirname "$0")/../.."
PORT="${1:-8766}"
RUNS="${ONSET_VIDEO_RUNS:-$HOME/onset/runs/video/dash-v2}"
mkdir -p "$RUNS"
exec ~/onset/.venv/bin/python tools/dashboard.py --runs "$RUNS" --port "$PORT"
