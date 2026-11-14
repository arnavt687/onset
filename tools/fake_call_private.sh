#!/usr/bin/env bash
# Live-ish test: run THIS checkout's agent under a private name, then a fake call against it.
# Never touches the Chert line: a different AGENT_NAME (never "onset"), a private LiveKit room
# made by tools/fake_call.py, and a private health port.
#
#   bash tools/fake_call_private.sh [fake_call args...]      e.g. --assert, --video FILE
#
# FLOWTEST_AGENT_NAME   agent name to register and dispatch (default onset-flowtest)
# FLOWTEST_HEALTH_PORT  the agent's health port (default 8091)
# Keys: .env.local in this checkout, else ~/onset/.env.local (read by Python, never printed).
# Exit code: fake_call.py's (with --assert: 0 only if audio worked both ways).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE" || exit 1
PY=${PY:-.venv/bin/python}; [ -x "$PY" ] || PY=$HOME/onset/.venv/bin/python
NAME="${FLOWTEST_AGENT_NAME:-onset-flowtest}"
if [ "$NAME" = "onset" ] || [ -z "$NAME" ]; then
  echo "refusing to run as agent name '$NAME' (that is the Chert line)"; exit 2
fi
export AGENT_NAME="$NAME" ONSET_PATIENT_ID="${ONSET_PATIENT_ID:-flowtest}"
export ONSET_HEALTH_PORT="${FLOWTEST_HEALTH_PORT:-8091}"
LOG="/tmp/${NAME}-agent.log"
echo "checkout $HERE, agent name $AGENT_NAME, health port $ONSET_HEALTH_PORT, log $LOG"

# agent.py loads only its own checkout's .env.local; a worktree may not have one, so load the
# main checkout's first. load_dotenv never overrides the environment: AGENT_NAME stays private.
"$PY" -c '
import os, sys
from pathlib import Path
from dotenv import load_dotenv
for p in (Path("agent.py").resolve().parent / ".env.local", Path.home() / "onset" / ".env.local"):
    if p.is_file():
        load_dotenv(p, override=False)
assert os.environ["AGENT_NAME"] != "onset"
os.execv(sys.executable, [sys.executable, "agent.py", "start"])  # same pid, keys in its environment
' > "$LOG" 2>&1 &
PID=$!
echo "agent pid $PID"
stop_agent() {
  kill -INT "$PID" 2>/dev/null; sleep 2; kill -INT "$PID" 2>/dev/null; sleep 3; kill -TERM "$PID" 2>/dev/null
  wait "$PID" 2>/dev/null
}
for _ in $(seq 1 60); do grep -q "registered worker" "$LOG" && break; kill -0 "$PID" 2>/dev/null || break; sleep 1; done
if ! grep -q "registered worker" "$LOG"; then
  echo "agent did not register"; tail -20 "$LOG"; stop_agent; exit 1
fi
"$PY" tools/fake_call.py 100 --speak --frames runs/fakecall-frames --save-every 3 "$@"
RC=$?
stop_agent
echo "--- agent log: onset lines and errors"
grep -oE '"message": "[^"]*(\[onset\]|rror|Traceback)[^"]{0,220}' "$LOG" | grep -v "caller video [0-9]" | head -60
echo "fake_call exit code $RC"
exit "$RC"
