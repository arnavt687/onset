#!/usr/bin/env bash
# Live-ish test: run this worktree's agent under a private name, then a fake call against it.
# Never touches the Chert line (a different AGENT_NAME, a private LiveKit room).
cd ~/onset-wt/flow
export AGENT_NAME=onset-flowtest ONSET_PATIENT_ID="${ONSET_PATIENT_ID:-flowtest}" ONSET_HEALTH_PORT=8091
LOG=/tmp/onset-flowtest-agent.log
~/onset/.venv/bin/python agent.py start > "$LOG" 2>&1 &
PID=$!
echo "agent pid $PID"
for i in $(seq 1 60); do grep -q "registered worker" "$LOG" && break; sleep 1; done
grep -q "registered worker" "$LOG" || { echo "agent did not register"; tail -20 "$LOG"; kill $PID; exit 1; }
~/onset/.venv/bin/python tools/fake_call.py 100 --speak --frames runs/fakecall-frames --save-every 3 "$@"
kill -INT $PID; sleep 2; kill -INT $PID 2>/dev/null; sleep 3; kill -TERM $PID 2>/dev/null
echo "--- agent log: onset lines and errors"
grep -oE '"message": "[^"]*(\[onset\]|rror|Traceback)[^"]{0,220}' "$LOG" | grep -v "caller video [0-9]" | head -60
