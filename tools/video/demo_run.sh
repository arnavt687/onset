#!/usr/bin/env bash
# Play one synthetic run into the video dashboard (nothing is sent anywhere).
#   bash tools/video/demo_run.sh escalate|no_change|unclear|first_run [speed] [hold_end]
set -euo pipefail
cd "$(dirname "$0")/../.."
RUNS="${ONSET_VIDEO_RUNS:-$HOME/onset/runs/video/dash}"
exec ~/onset/.venv/bin/python tools/demo_events.py --outcome "$1" --runs "$RUNS" \
  --speed "${2:-1}" --hold-end "${3:-8}" --caregiver dry_run
