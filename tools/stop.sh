#!/usr/bin/env bash
# Stop chert dev (Ctrl+C: frees the line) and the Onset agent.
DEV='node .*/chert dev'
AGENT='python[0-9.]* agent.py (start|dev)'

wait_gone() { for _ in $(seq 1 "$2"); do pgrep -f "$1" >/dev/null || return 0; sleep 1; done; return 1; }

if pgrep -f "$DEV" >/dev/null; then
  pkill -INT -f "$DEV"
  wait_gone "$DEV" 20 && echo "chert dev stopped" || { echo "chert dev did not stop on Ctrl+C; sending TERM"; pkill -TERM -f "$DEV"; }
else
  echo "chert dev not running"
fi

if pgrep -f "$AGENT" >/dev/null; then
  # The first Ctrl+C drains (waits for jobs, up to an hour for idle waiting rooms);
  # a second one exits now. Waiting rooms hold no caller once chert dev is gone.
  pkill -INT -f "$AGENT"; sleep 2; pkill -INT -f "$AGENT"
  wait_gone "$AGENT" 15 && echo "agent stopped" || { echo "agent did not stop; sending TERM"; pkill -TERM -f "$AGENT"; wait_gone "$AGENT" 5 && echo "agent stopped"; }
else
  echo "agent not running"
fi
pgrep -af "$DEV|$AGENT|npm exec @trychert" || echo "nothing left running"
