#!/usr/bin/env bash
# Merge the piece branches into main in dependency order, dropping per-session scratch scripts.
set -uo pipefail
cd ~/onset
git fetch -q origin
git checkout -q main && git pull -q --ff-only origin main
for b in harness decide face arms speech chert-api; do
  echo "=== merging piece/$b"
  if ! git merge --no-ff -q "origin/piece/$b" -m "Merge piece/$b" 2>/dev/null; then
    # Scratch scripts from sessions collide (notes/run.sh); keep none of them.
    git rm -q --cached -f notes/run.sh 2>/dev/null; rm -f notes/run.sh
    if git diff --name-only --diff-filter=U | grep -q .; then
      echo "unresolved conflicts:"; git diff --name-only --diff-filter=U; exit 1
    fi
    git commit -q --no-edit && echo "  (resolved: dropped notes/run.sh)"
  fi
done
git rm -q --cached -f notes/run.sh 2>/dev/null && { rm -f notes/run.sh; git commit -q -m "Drop session scratch script"; }
echo "=== full test suite on merged main"
.venv/bin/python -m pytest -q 2>&1 | tail -5
