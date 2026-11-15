#!/usr/bin/env bash
# Test the caregiver briefing call without Chert: run THIS checkout's agent under a private name,
# then tools/caregiver_call.py --fake-bridge, which plays Chert's side (joins the room, says
# "bridge ready") and reports what the caregiver would hear and see.
#
#   bash tools/fake_brief_private.sh [caregiver_call args...]
#
# Never touches the Chert line: a private AGENT_NAME (never "onset"), its own LiveKit room and
# health port. Keys: .env.local here, else ~/onset/.env.local (read by Python, never printed).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE" || exit 1
PY=${PY:-.venv/bin/python}; [ -x "$PY" ] || PY=$HOME/onset/.venv/bin/python
NAME="${FLOWTEST_AGENT_NAME:-onset-brieftest}"
if [ "$NAME" = "onset" ] || [ -z "$NAME" ]; then echo "refusing to run as agent name '$NAME'"; exit 2; fi
export AGENT_NAME="$NAME" ONSET_HEALTH_PORT="${FLOWTEST_HEALTH_PORT:-8093}"
LOG="/tmp/${NAME}-agent.log"
"$PY" -c '
import os, sys
from pathlib import Path
from dotenv import load_dotenv
for p in (Path("agent.py").resolve().parent / ".env.local", Path.home() / "onset" / ".env.local"):
    if p.is_file():
        load_dotenv(p, override=False)
os.execv(sys.executable, [sys.executable, "agent.py", "start"])
' > "$LOG" 2>&1 &
PID=$!
stop_agent() { kill -INT "$PID" 2>/dev/null; sleep 2; kill -INT "$PID" 2>/dev/null; sleep 2; kill -TERM "$PID" 2>/dev/null; }
trap stop_agent EXIT
for _ in $(seq 1 120); do grep -q "registered worker" "$LOG" 2>/dev/null && break; sleep 0.5; done
grep -q "registered worker" "$LOG" || { echo "the agent did not register (see $LOG)"; exit 1; }
"$PY" tools/caregiver_call.py --fake-bridge --agent-name "$NAME" --patient Alex --caregiver Sam \
  --reason "The left arm dropped while it was held up" --reason "The smile was less even than usual" "$@"
rc=$?
echo "--- agent log: briefing lines"
grep -o '"message": "\[onset\] [^"]*' "$LOG" | grep -i 'brief\|caregiver' | cut -c13-200
exit $rc
