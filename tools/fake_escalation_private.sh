#!/usr/bin/env bash
# Run this checkout's agent under a private name, then tools/fake_escalation.py against it.
# Never touches the Chert line (a different AGENT_NAME, a private LiveKit room) and never sends
# anything (ONSET_LIVE_ACTIONS=0, no caregiver phone).
cd "$(dirname "$0")/.." || exit 1
ROOT=$PWD
PY=${PY:-$ROOT/.venv/bin/python}; [ -x "$PY" ] || PY=$HOME/onset/.venv/bin/python
export AGENT_NAME=${ESCTEST_AGENT_NAME:-onset-esctest} ONSET_HEALTH_PORT=${ESCTEST_HEALTH_PORT:-8092}
export ONSET_PATIENT_ID=esctest ONSET_PATIENT_NAME="Esc Test" ONSET_CAREGIVER_PHONE= ONSET_LIVE_ACTIONS=0
unset ONSET_PATIENTS_FILE   # the env profile above (this checkout has no config/patients.json)
export ONSET_CAREGIVER_JOIN=link ONSET_CAREGIVER_TALK=1 ONSET_CAREGIVER_PAGE=https://example.invalid/caregiver/
export ONSET_RUNS_DIR=$ROOT/runs
mkdir -p "$ONSET_RUNS_DIR" && chmod 700 "$ONSET_RUNS_DIR"
# A baseline for the test patient, so the result is a comparison (UNCLEAR), not a first call.
"$PY" - <<'PY'
from onset import decide
from onset.types import Baseline
decide.save_baseline(Baseline(patient_id="esctest", recorded_at="2026-01-01T00:00:00+00:00", face_asymmetry=0.05,
    arm_drift_difference=0.02, speech_word_error_rate=0.0, speech_words_per_minute=130.0,
    sentence="You can't teach an old dog new tricks.", samples=2))
PY
LOG=$ROOT/runs/esctest-agent.log
"$PY" agent.py start >"$LOG" 2>&1 &
PID=$!
for _ in $(seq 1 60); do grep -q "registered worker" "$LOG" && break; sleep 1; done
grep -q "registered worker" "$LOG" || { echo "agent did not register"; tail -20 "$LOG"; kill $PID; exit 1; }
"$PY" tools/fake_escalation.py "$@"; rc=$?
kill -INT $PID; sleep 2; kill -INT $PID 2>/dev/null; sleep 3; kill -TERM $PID 2>/dev/null
echo "--- agent log: onset lines and errors"
grep -oE '"message": "[^"]*(\[onset\]|rror|Traceback)[^"]{0,200}' "$LOG" | grep -v "caller video [0-9]" | tail -40
exit $rc
