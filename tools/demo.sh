#!/usr/bin/env bash
# One command for demo day. Run inside WSL Ubuntu from anywhere:
#
#   tools/demo.sh                 tools/doctor.py --warm, then agent + chert dev + dashboard; Ctrl+C stops all three
#   tools/demo.sh --check         only tools/doctor.py (keys, voice, models, LiveKit, Chert line, clock); starts nothing
#   tools/demo.sh --replay DIR [-- --fast --no-voice ...]
#                                 dashboard + tools/replay_flow.py DIR (no call, no Chert line);
#                                 anything after -- goes to replay_flow.py
# Options: --port N (dashboard, default 8765)  --open (open the dashboard in the Windows browser)
#          --skip-clock (start even if a clock check fails)
#
# Never prints key values: tools/doctor.py reports names only, masks phone numbers and never shows
# the signed-in Chert account. Python: $PY, else this checkout's .venv (tools/setup.sh), else ~/onset/.venv.
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT=$PWD
if [ -z "${PY:-}" ]; then
  PY=$ROOT/.venv/bin/python
  [ -x "$PY" ] || PY=$HOME/onset/.venv/bin/python
fi
[ -x "$PY" ] || { echo "[demo] no Python virtualenv: run tools/setup.sh" >&2; exit 1; }
PORT=8765
MODE=live
OPEN=0
SKIP_CLOCK=0
REPLAY_DIR=
REPLAY_FLOW=${REPLAY_FLOW:-$ROOT/tools/replay_flow.py}   # overridable for testing
LOGS=$ROOT/runs/demo-logs
STAMP=$(date +%Y%m%d-%H%M%S)

REPLAY_ARGS=()
usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; }
say() { printf '\033[1m[demo]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[demo] %s\033[0m\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --check|--dry-run) MODE=check ;;
    --replay) MODE=replay; REPLAY_DIR=${2:-}; [ -n "$REPLAY_DIR" ] || die "--replay needs a recording directory"; shift ;;
    --port) PORT=${2:-}; [[ "$PORT" =~ ^[0-9]+$ ]] || die "--port needs a number"; shift ;;
    --open) OPEN=1 ;;
    --skip-clock) SKIP_CLOCK=1 ;;
    --) shift; REPLAY_ARGS=("$@"); break ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
  shift
done
mkdir -p "$LOGS"
chmod 700 "$ROOT/runs" "$LOGS" 2>/dev/null

# ---------------------------------------------------------------- checks

# tools/doctor.py: every check, PASS/WARN/FAIL lines, exit 1 on any FAIL. Sets LINE_NUMBER.
run_doctor() {
  local line_file=$LOGS/line-$STAMP rc
  local args=(--port "$PORT" --line-file "$line_file" "$@")
  [ "$SKIP_CLOCK" = 1 ] && args+=(--skip-clock)
  say "checking everything (tools/doctor.py${*:+ $*})..."
  "$PY" tools/doctor.py "${args[@]}"
  rc=$?
  LINE_NUMBER=$(cat "$line_file" 2>/dev/null)
  rm -f "$line_file"
  return $rc
}

already_running() {
  pgrep -af 'python agent.py start|node .*/chert dev|tools/dashboard.py' | grep -v pgrep || true
}

# ---------------------------------------------------------------- processes

PIDS_AGENT= PIDS_DEV= PIDS_DASH=
wait_for() {  # wait_for <file> <pattern> <seconds> <pid>
  local i
  for i in $(seq 1 $(( $3 * 2 ))); do
    grep -q "$2" "$1" 2>/dev/null && return 0
    [ -n "$4" ] && ! alive "$4" && return 2
    sleep 0.5
  done
  return 1
}
# Each child runs in its own session (setsid), so its pid is also its process group id:
# signals go to the whole group (npx and the node it starts), like Ctrl+C in a terminal.
alive() { pgrep -g "$1" >/dev/null 2>&1; }
gone() { local i; for i in $(seq 1 $(( $2 * 2 ))); do alive "$1" || return 0; sleep 0.5; done; return 1; }

start_dashboard() {
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    die "something already answers on port $PORT (a dashboard from an earlier run? tools/stop.sh does not stop it: pkill -f tools/dashboard.py)"
  fi
  setsid "$PY" tools/dashboard.py --port "$PORT" >"$LOGS/dashboard-$STAMP.log" 2>&1 </dev/null &
  PIDS_DASH=$!
  local i
  for i in $(seq 1 20); do
    curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && return 0
    kill -0 "$PIDS_DASH" 2>/dev/null || break
    sleep 0.5
  done
  tail -5 "$LOGS/dashboard-$STAMP.log" >&2
  die "the dashboard did not start"
}

stop_all() {
  trap - INT TERM
  echo
  if [ -n "$PIDS_DEV" ] && alive "$PIDS_DEV"; then
    say "stopping chert dev (Ctrl+C, so the line is released)..."
    kill -INT -- "-$PIDS_DEV" 2>/dev/null
    if wait_for "$LOGS/chert-dev-$STAMP.log" '"dev_stopped"' 20 "$PIDS_DEV" || gone "$PIDS_DEV" 5; then
      say "chert dev stopped"
    else
      say "chert dev did not stop on Ctrl+C; sending TERM (still releases the line)"
      kill -TERM -- "-$PIDS_DEV" 2>/dev/null; gone "$PIDS_DEV" 10
    fi
    grep -q '"release_failed"' "$LOGS/chert-dev-$STAMP.log" 2>/dev/null && say "WARNING: chert reported release_failed; the line is held until its lease lapses"
  fi
  if [ -n "$PIDS_AGENT" ] && alive "$PIDS_AGENT"; then
    say "stopping the agent..."
    # The first Ctrl+C drains; a second one exits now (no caller can arrive once chert dev is gone).
    kill -INT -- "-$PIDS_AGENT" 2>/dev/null; sleep 2; kill -INT -- "-$PIDS_AGENT" 2>/dev/null
    gone "$PIDS_AGENT" 15 || { kill -TERM -- "-$PIDS_AGENT" 2>/dev/null; gone "$PIDS_AGENT" 5; }
    say "agent stopped"
  fi
  if [ -n "$PIDS_DASH" ] && alive "$PIDS_DASH"; then
    kill -TERM -- "-$PIDS_DASH" 2>/dev/null; gone "$PIDS_DASH" 5 || kill -KILL -- "-$PIDS_DASH" 2>/dev/null
    say "dashboard stopped"
  fi
  say "logs: $LOGS/*-$STAMP.log"
  exit 0
}

open_browser() {
  [ "$OPEN" = 1 ] || return 0
  if command -v cmd.exe >/dev/null 2>&1; then
    (cd /mnt/c 2>/dev/null && cmd.exe /c start "" "http://localhost:$PORT" >/dev/null 2>&1) || true
  fi
}

banner() {
  echo
  printf '\033[1;44m  ONSET DEMO READY  \033[0m\n'
  [ -n "${LINE_NUMBER:-}" ] && printf '  FaceTime the line:  \033[1m%s\033[0m\n' "$LINE_NUMBER"
  printf '  Dashboard:          \033[1mhttp://localhost:%s\033[0m  (click once to enable sound)\n' "$PORT"
  printf '  Stop everything:    Ctrl+C here\n\n'
}

# ---------------------------------------------------------------- modes

if [ "$MODE" = check ]; then
  run_doctor; rc=$?
  r=$(already_running); [ -n "$r" ] && say "already running (would block a start):" && echo "$r"
  say "checks done; nothing was started"
  exit $rc
fi

if [ "$MODE" = replay ]; then
  [ -d "$REPLAY_DIR" ] || die "no such recording directory: $REPLAY_DIR"
  [ -f "$REPLAY_FLOW" ] || die "$REPLAY_FLOW is missing (the flow piece adds tools/replay_flow.py)"
  trap stop_all INT TERM
  start_dashboard
  say "dashboard up: http://localhost:$PORT"
  open_browser
  say "replaying $REPLAY_DIR ..."
  "$PY" "$REPLAY_FLOW" "$REPLAY_DIR" "${REPLAY_ARGS[@]}" &
  wait $! ; rc=$?
  [ $rc -eq 0 ] && say "replay finished" || say "replay exited with code $rc"
  say "the dashboard stays up with the result; Ctrl+C to stop"
  while kill -0 "$PIDS_DASH" 2>/dev/null; do sleep 1; done
  exit 0
fi

# live
r=$(already_running)
[ -n "$r" ] && { echo "$r" >&2; die "agent, chert dev or a dashboard is already running: stop it first (tools/stop.sh, pkill -f tools/dashboard.py)"; }
# --warm puts every scripted line in the TTS cache, so Onset greets within Chert's 10 s deadline.
run_doctor --warm || die "fix the FAIL lines above, then run again (tools/demo.sh --check re-checks; a busy line_free means another chert dev session holds the line: this script never uses --takeover)"
trap stop_all INT TERM

say "starting the agent (log: $LOGS/agent-$STAMP.log)..."
setsid "$PY" agent.py start >"$LOGS/agent-$STAMP.log" 2>&1 </dev/null &
PIDS_AGENT=$!
wait_for "$LOGS/agent-$STAMP.log" "registered worker" 60 "$PIDS_AGENT"
case $? in
  0) say "agent registered with LiveKit" ;;
  2) tail -20 "$LOGS/agent-$STAMP.log" >&2; say "the agent exited"; stop_all ;;
  *) tail -20 "$LOGS/agent-$STAMP.log" >&2; say "no 'registered worker' after 60 s"; stop_all ;;
esac

say "connecting the line (chert dev; log: $LOGS/chert-dev-$STAMP.log)..."
setsid bash tools/chert.sh dev --json >"$LOGS/chert-dev-$STAMP.log" 2>&1 </dev/null &
PIDS_DEV=$!
for _ in $(seq 1 240); do
  grep -q '"agent_warm"' "$LOGS/chert-dev-$STAMP.log" && break
  if grep -qE '"(line_in_use|line_in_call|displaced|agent_not_warm|login_required)"' "$LOGS/chert-dev-$STAMP.log"; then
    grep -E '"code"' "$LOGS/chert-dev-$STAMP.log" | "$PY" -c 'import json,re,sys
email = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
for l in sys.stdin:
    try: r=json.loads(l)
    except ValueError: continue
    print(email.sub("<account>", "[demo]   chert: %s - %s" % (r.get("code"), r.get("message") or r.get("action") or "")))' >&2
    say "chert dev cannot take the line"; stop_all
  fi
  alive "$PIDS_DEV" || { tail -5 "$LOGS/chert-dev-$STAMP.log" >&2; say "chert dev exited"; stop_all; }
  sleep 0.5
done
grep -q '"agent_warm"' "$LOGS/chert-dev-$STAMP.log" || { say "no agent_warm after 120 s"; stop_all; }
say "agent warm: the line is answering"

start_dashboard
open_browser
banner
while :; do
  alive "$PIDS_AGENT" || { say "the agent exited (see its log)"; stop_all; }
  alive "$PIDS_DEV" || { say "chert dev exited (see its log)"; stop_all; }
  sleep 1
done
