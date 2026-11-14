#!/usr/bin/env bash
# Merge one origin branch into main, run the tests, push. Usage: tools/merge-branch.sh <branch> [<branch>...]
set -uo pipefail
cd "$(dirname "$0")/.."
git fetch -q origin
git checkout -q main && git pull -q --ff-only origin main || { echo "main not fast-forwardable"; exit 1; }
for b in "$@"; do
  echo "=== merging $b"
  if ! git merge --no-ff -q "origin/$b" -m "Merge $b"; then
    echo "conflicts:"; git diff --name-only --diff-filter=U; git merge --abort; exit 1
  fi
done
git ls-files --cached | grep -qE '^\.env\.local$|^models/|^recordings/|^baselines/|^runs/' && { echo "private file in tree; not pushing"; exit 1; }
.venv/bin/python -m pytest -q tests 2>&1 | tail -3
git push -q origin main && git log --oneline -3
