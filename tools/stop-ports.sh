#!/usr/bin/env bash
# Stop whatever listens on the given local ports (helper servers left by capture jobs). Usage: tools/stop-ports.sh 8766 8772
for p in "$@"; do
  pids=$(ss -ltnp 2>/dev/null | awk -v port=":$p" '$4 ~ port"$" {print $0}' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u)
  if [ -n "$pids" ]; then kill $pids && echo "port $p: stopped $pids"; else echo "port $p: nothing listening"; fi
done
