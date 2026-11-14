#!/usr/bin/env bash
# Commit any tracked-tool changes on main, scan for secrets, push, and show PR + CI state.
set -euo pipefail
cd ~/onset
msg="${1:-Update tools}"
git add -A
git ls-files --cached | grep -qE '^\.env\.local$|^models/|^recordings/|^baselines/' && { echo "private file staged; aborting"; exit 1; }
if git diff --cached -U0 | grep -nE '(AQ\.[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}|sk_[A-Za-z0-9]{20,}|ftk_[A-Za-z0-9]{10,})'; then
  echo "possible secret; aborting"; exit 1
fi
if ! git diff --cached --quiet; then
  git commit -q -F - <<MSG
$msg

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
fi
git push -q origin main
git log --oneline -3
sleep 5
gh pr list --state all --json number,state,title -q '.[] | "#\(.number) \(.state) \(.title)"' | head -8
gh run list --branch main --limit 1 --json status,conclusion,displayTitle -q '.[] | "CI on main: \(.status) \(.conclusion // "")"'
