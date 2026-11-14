#!/usr/bin/env bash
set -euo pipefail
cd ~/onset
.venv/bin/python -m pytest -q tests 2>&1 | tail -3
git add -A
git ls-files --cached | grep -qE '^\.env\.local$|^models/' && { echo "secret or model staged; aborting"; exit 1; }
git status --short
git commit -q -F - <<'MSG'
Add development map, module contracts and stubs

ROADMAP.md maps the pieces (face, arms, speech, decide, flow, caregiver
iMessage, recorder/CI, outbound calls) with owners, file boundaries and
done criteria. onset/types.py holds the shared contracts; each module has
a stub with its interface. WORKING.md explains worktrees and commands for
the parallel sessions. MediaPipe and pytest are added to requirements.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
git push -q origin main
git log --oneline -2
