#!/usr/bin/env bash
set -e
cd ~/onset && git fetch -q origin
[ -d ~/onset-wt/flow ] || git worktree add -q ~/onset-wt/flow -b piece/flow origin/main
cd ~/onset-wt/flow && git log --oneline -1 && ls onset onset/measure tools notes tests
