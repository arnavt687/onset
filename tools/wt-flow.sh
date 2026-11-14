#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.." && git fetch -q origin
WT=${ONSET_WT:-$HOME/onset-wt}/flow
[ -d "$WT" ] || git worktree add -q "$WT" -b piece/flow origin/main
cd "$WT" && git log --oneline -1 && ls onset onset/measure tools notes tests
