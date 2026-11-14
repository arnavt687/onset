#!/usr/bin/env bash
# notes/flow-commit.sh "<message>": test, commit the flow files and push piece/flow.
set -euo pipefail
cd ~/onset-wt/flow
rm -f notes/dbg.py notes/probe.sh
~/onset/.venv/bin/python -m pytest -q tests 2>&1 | tail -2
for p in onset tests tools agent.py notes/flow.md notes/flow-commit.sh; do [ -e "$p" ] && git add "$p"; done
git add -u
if git diff --cached -U0 | grep -nE '(AQ\.[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}|sk_[A-Za-z0-9]{20,}|ftk_[A-Za-z0-9]{10,})'; then echo "secret?"; exit 1; fi
git ls-files --cached | grep -qE '^(\.env\.local|runs/|recordings/|baselines/|models/)' && { echo "private file staged"; exit 1; }
git diff --cached --quiet && { echo "nothing to commit"; exit 0; }
git commit -q -F - <<MSG
flow: $1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
git push -q -u origin piece/flow 2>&1 | tail -1
git log --oneline -1
